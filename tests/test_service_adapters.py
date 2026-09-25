"""No network, model, Azure SDK, training code, or GPU resources are used."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import create_autospec, patch

import yaml
from copilot import CopilotClient, CopilotSession, GetAuthStatusResponse, ToolInvocation
from copilot.rpc import PermissionDecisionReject

from research_intern.contracts.loader import load_contract
from research_intern.controller.handoff import build_iteration_context
from research_intern.controller.readiness import project_readiness
from research_intern.copilot import live
from research_intern.copilot.files import ControlledFiles, SourceAccessError
from research_intern.copilot.spike import SpikeError
from research_intern.domain.experiments import JobRequest, SliceError
from research_intern.domain.research import CandidatePlan
from research_intern.execution.adapter import ExecutionError
from research_intern.execution.azure import AzureExecutor, AzureSettings, RemoteJob
from research_intern.execution.simulated import write_outputs
from research_intern.controller.execution import ExecutionController
from research_intern.execution.simulated import SimulatedExecutor
from research_intern.ledger.research_state import build_research_state
from research_intern.ledger.services import ServiceAdmissionError, ServiceJournal
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.git import GitWorkspace

from test_execution_slice import WorkspaceTest


class ServiceFixture(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        for name, content in {
            "train.py": "RATE = 0.1\n", "evaluate.py": "# Protected evaluator fixture\n",
            "split.json": "{}\n", "configs/baseline.yaml": "rate: 0.1\n",
            "configs/candidate.yaml": "rate: 0.1\n",
        }.items():
            path = self.repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode())
        protocol = {"metric_definition": "Fixture F1", "procedure": "Fixed fixture protocol",
                    "dataset_version": "fixture-v1", "evaluator": "evaluate.py", "validation_split": "split.json"}
        for name in ("evaluator", "validation_split"):
            protocol[name + "_sha256"] = hashlib.sha256((self.repository / protocol[name]).read_bytes()).hexdigest()
        contract = {"version": "1.0", "objective": {"metric": "f1", "direction": "maximize"},
                    "execution": {"backend": "azure_ml", "job_config": "job.yaml"},
                    "outputs": {"root": "experiment_outputs/"},
                    "scope": {"editable": ["train.py", "configs/candidate.yaml"], "protected": ["evaluate.py", "split.json"]},
                    "budget": {"max_experiments": 3, "max_gpu_hours": 1, "max_ai_credits": 1},
                    "evaluation": protocol}
        path = self.repository / ".research_intern/contract.yaml"
        path.parent.mkdir()
        path.write_bytes(yaml.safe_dump(contract).encode())
        job = {"type": "command", "command": "echo fixture-only",
               "inputs": {key: "fixture" for key in ("config", "dataset", "weights", "experiment_id", "parent_experiment", "source_commit")},
               "outputs": {"experiment_outputs": {"type": "uri_folder"}}}
        (self.repository / "job.yaml").write_bytes(yaml.safe_dump(job).encode())
        self.contract = load_contract(self.repository)
        self.settings = AzureSettings("00000000-0000-0000-0000-000000000000", "fixture-rg", "fixture-ws",
                                      "fixture-gpu", "azureml:env:1", "example.invalid/image@sha256:" + "a" * 64,
                                      "azureml:data:1", "azureml:weights:1", 60, 1)


class SourceCapabilityTests(ServiceFixture):
    def test_only_reviewed_paths_are_readable(self):
        files = ControlledFiles(self.repository, self.contract, ("train.py", "evaluate.py"))
        self.assertIn("RATE", files.read("train.py")["text"])
        for name in ("../train.py", ".git/config", "split.json", "Train.py", "configs/candidate.yaml"):
            with self.subTest(name=name), self.assertRaises(SliceError):
                files.read(name)

    def test_writes_require_scope_and_current_hash(self):
        files = ControlledFiles(self.repository, self.contract, ("train.py", "evaluate.py"))
        current = files.read("train.py")
        files.write("train.py", current["sha256"], "RATE = 0.2\n")
        with self.assertRaises(SourceAccessError):
            files.write("train.py", current["sha256"], "RATE = 0.3\n")
        protected = files.read("evaluate.py")
        with self.assertRaises(SourceAccessError):
            files.write("evaluate.py", protected["sha256"], "tampered")
        self.assertEqual(files.read("evaluate.py"), protected)

    def test_hardlinks_binary_and_oversize_are_rejected(self):
        files = ControlledFiles(self.repository, self.contract, ("train.py",))
        original = self.repository / "train.py"
        os.link(original, self.root / "alias")
        with self.assertRaises(SourceAccessError):
            files.read("train.py")
        (self.root / "alias").unlink()
        for content in (b"\xff", b"x" * (128 * 1024 + 1)):
            original.write_bytes(content)
            with self.assertRaises(SourceAccessError):
                files.read("train.py")


class ServiceJournalTests(WorkspaceTest):
    def test_concurrent_admission_cannot_overspend(self):
        journal = ServiceJournal(self.root, service="azure", unit="gpu_seconds", max_units=60)

        def reserve(number):
            try:
                journal.reserve(f"EXP-{number:03d}", {"number": number}, units=60)
                return True
            except ServiceAdmissionError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, (1, 2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(journal.usage()["reserved"], 60)

    def test_immutable_allowance_idempotence_and_unknown_usage(self):
        journal = ServiceJournal(self.root, service="azure", unit="gpu_seconds", max_units=120)
        intent, created = journal.reserve("EXP-001", {"commit": "a"}, units=60)
        self.assertTrue(created)
        restarted = ServiceJournal(self.root, service="azure", unit="gpu_seconds", max_units=120)
        again, created = restarted.reserve("EXP-001", {"commit": "a"}, units=60)
        self.assertFalse(created)
        self.assertEqual(intent, again)
        self.assertEqual(restarted.usage()["reserved"], 60)
        self.assertIsNone(restarted.usage()["actual_usage"])
        self.assertFalse(restarted.usage()["billing_cap"])
        for service, unit, limit in (("azure", "gpu_seconds", 121), ("azure", "hours", 120)):
            with self.assertRaises(ServiceAdmissionError):
                ServiceJournal(self.root, service=service, unit=unit, max_units=limit)

    def test_exhaustion_and_rebinding_never_refund_or_overwrite(self):
        journal = ServiceJournal(self.root, service="copilot", unit="turns", max_units=1)
        journal.reserve("attempt-1", {"parent": "a"}, units=1)
        with self.assertRaises(ServiceAdmissionError):
            journal.reserve("attempt-2", {"parent": "a"}, units=1)
        with self.assertRaises(ServiceAdmissionError):
            journal.reserve("attempt-1", {"parent": "b"}, units=1)
        journal.attach("attempt-1", "session-1")
        journal.attach("attempt-1", "session-1")
        with self.assertRaises(ServiceAdmissionError):
            journal.attach("attempt-1", "session-2")
        self.assertEqual(journal.get("attempt-1").remote_id, "session-1")
        self.assertEqual(journal.usage()["remaining"], 0)


class FakeAzure:
    def __init__(self):
        self.jobs = {}
        self.submissions = []
        self.cancelled = []
        self.interrupt = False

    def submit(self, name, code, payload):
        self.submissions.append((name, code, copy.deepcopy(payload)))
        job = RemoteJob(name, "Queued", AzureExecutor._tags(payload))
        self.jobs[name] = job
        if self.interrupt:
            raise TimeoutError("Response lost AFTER job creation")
        return job

    def get(self, name):
        return self.jobs.get(name)

    def download(self, name, destination):
        root = destination / "named-outputs/experiment_outputs"
        root.mkdir(parents=True)
        (root / "run.json").write_text('{"fixture": true}')
        return root

    def cancel(self, name):
        self.cancelled.append(name)


class AzureAdapterTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.git = GitWorkspace.initialize(self.root, self.repository)
        self.request = JobRequest("EXP-000", None, self.git.head)
        self.journal = ServiceJournal(self.root, service="azure", unit="gpu_seconds", max_units=120)
        self.gateway = FakeAzure()
        self.executor = self.compose()

    def compose(self):
        return AzureExecutor(self.git, self.settings, self.journal, self.gateway, live_authorized=True)

    def test_opt_in_pinned_assets_and_request_validation(self):
        with self.assertRaises(ExecutionError):
            AzureExecutor(self.git, self.settings, self.journal, self.gateway)
        for change in ({"environment": "azureml:env:latest"}, {"image": "image:latest"},
                       {"dataset": "https://example.invalid?sig=secret"}, {"timeout_seconds": True}, {"gpu_count": 0}):
            with self.subTest(change=change), self.assertRaises(ExecutionError):
                replace(self.settings, **change)
        with self.assertRaises(ExecutionError):
            self.executor.submit_job(replace(self.request, git_commit="HEAD"))
        self.assertEqual(self.gateway.submissions, [])

    def test_exact_source_and_intent_exist_before_network(self):
        original = self.gateway.submit

        def submit(name, code, payload):
            saved = self.journal.get("EXP-000")
            self.assertEqual(saved.name, name)
            self.assertIsNone(saved.remote_id)
            self.assertEqual(self.journal.usage()["reserved"], 60)
            self.assertEqual((code / "source/train.py").read_bytes(), (self.repository / "train.py").read_bytes())
            self.assertFalse((code / "source/.git").exists())
            self.assertEqual(json.loads((code / "source_manifest.json").read_text()), self.git._tree(self.git.head))
            return original(name, code, payload)

        with patch.object(self.gateway, "submit", side_effect=submit):
            name = self.executor.submit_job(self.request)
        self.assertEqual(self.journal.get("EXP-000").remote_id, name)
        self.assertEqual(self.executor.get_status(name), "queued")
        self.assertEqual(self.compose().submit_job(self.request), name)
        self.assertEqual(len(self.gateway.submissions), 1)

    def test_ambiguous_submission_reconciles_without_resubmitting(self):
        self.gateway.interrupt = True
        with self.assertRaises(TimeoutError):
            self.executor.submit_job(self.request)
        intent = self.journal.get("EXP-000")
        self.assertIsNone(intent.remote_id)
        self.assertEqual(self.compose().submit_job(self.request), intent.name)
        self.assertEqual(len(self.gateway.submissions), 1)
        self.assertEqual(self.journal.usage()["reserved"], 60)

    def test_missing_remote_or_conflicting_tags_never_resubmits(self):
        name = self.executor.submit_job(self.request)
        job = self.gateway.jobs.pop(name)
        with self.assertRaisesRegex(ExecutionError, "unresolved"):
            self.compose().submit_job(self.request)
        self.gateway.jobs[name] = replace(job, tags={})
        with self.assertRaisesRegex(ExecutionError, "intent"):
            self.compose().submit_job(self.request)
        self.assertEqual(len(self.gateway.submissions), 1)

    def test_dirty_workspace_prevents_submission(self):
        (self.repository / "untracked.txt").write_text("not committed")
        with self.assertRaises(SliceError):
            self.executor.submit_job(self.request)
        self.assertEqual(self.gateway.submissions, [])
        self.assertEqual(self.journal.usage()["reserved"], 0)

    def test_budget_denial_and_settings_drift_prevent_remote_submission(self):
        expensive = AzureExecutor(self.git, replace(self.settings, timeout_seconds=121),
                                  self.journal, self.gateway, live_authorized=True)
        with self.assertRaises(ServiceAdmissionError):
            expensive.submit_job(self.request)
        self.assertEqual(self.gateway.submissions, [])
        self.assertEqual(self.journal.usage()["reserved"], 0)

    def test_saved_intent_cannot_use_a_new_environment(self):
        self.executor.submit_job(self.request)
        changed = AzureExecutor(self.git, replace(self.settings, environment="azureml:env:2"),
                                self.journal, self.gateway, live_authorized=True)
        with self.assertRaisesRegex(ExecutionError, "rebound"):
            changed.submit_job(self.request)
        self.assertEqual(len(self.gateway.submissions), 1)

    def test_foreign_output_destination_and_bundle_are_rejected(self):
        name = self.executor.submit_job(self.request)
        self.gateway.jobs[name] = replace(self.gateway.jobs[name], status="Completed")
        with self.assertRaises(ExecutionError):
            self.executor.download_outputs(name, self.root / "foreign-output")
        destination = self.root / "experiments/EXP-000/experiment_outputs"
        with patch.object(self.gateway, "download", return_value=self.repository):
            with self.assertRaisesRegex(ExecutionError, "escaped"):
                self.executor.download_outputs(name, destination)
        self.assertFalse(destination.exists())

    def test_status_cancellation_and_atomic_collection(self):
        name = self.executor.submit_job(self.request)
        self.executor.cancel_job(name)
        self.assertEqual(self.gateway.cancelled, [name])
        destination = self.root / "experiments/EXP-000/experiment_outputs"
        with self.assertRaises(ExecutionError):
            self.executor.download_outputs(name, destination)
        for remote, local in (("Running", "running"), ("Completed", "completed"), ("Canceled", "cancelled"), ("Failed", "failed")):
            self.gateway.jobs[name] = replace(self.gateway.jobs[name], status=remote)
            self.assertEqual(self.executor.get_status(name), local)
        with patch.object(self.gateway, "download", side_effect=TimeoutError("download interrupted")):
            with self.assertRaises(TimeoutError):
                self.executor.download_outputs(name, destination)
        self.assertFalse(destination.exists())
        self.executor.download_outputs(name, destination)
        self.assertTrue((destination / "run.json").is_file())
        with self.assertRaises(ExecutionError):
            self.executor.download_outputs(name, destination)
        with self.assertRaises(ExecutionError):
            self.executor.cancel_job("unrelated-job")
        self.gateway.jobs[name] = replace(self.gateway.jobs[name], status="Unexpected")
        with self.assertRaises(ExecutionError):
            self.executor.get_status(name)


class LiveCopilotTests(ServiceFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        ledger = Ledger(self.root, self.contract.rules, max_experiments=3)
        self.addCleanup(ledger.close)
        request = JobRequest("EXP-000", None, "a" * 40)
        write_outputs(ledger.outputs("EXP-000"), self.contract.rules, request, score=0.5)
        ExecutionController(ledger, SimulatedExecutor(self.root, self.contract.rules)).import_baseline(request.git_commit)
        state = replace(build_research_state(ledger.snapshot()), mode="live")
        self.context = replace(build_iteration_context(state), contract=self.contract, attempt_number=1)
        self.journal = ServiceJournal(self.root, service="copilot", unit="turns", max_units=3)
        self.runtime = self.root / "fake-runtime"
        self.runtime.write_bytes(b"Never executed")
        self.client = create_autospec(CopilotClient, instance=True)
        self.session = create_autospec(CopilotSession, instance=True)
        self.client.get_auth_status.return_value = GetAuthStatusResponse(isAuthenticated=True)
        self.client.create_session.return_value = self.session
        constructor = patch.object(live, "CopilotClient", return_value=self.client)
        self.constructor = constructor.start()
        self.addCleanup(constructor.stop)
        self.plan = CandidatePlan("EXP-000", "Baseline evidence", "Tentative diagnosis", "A smaller rate may help",
                                  "Reduce rate", "Measure F1 externally")
        self.session.send_and_wait.side_effect = self.turn

    def adapter(self, **changes):
        kwargs = dict(workspace=self.root, runtime_path=self.runtime, model="mock-model",
                      readable_paths=("train.py", "evaluate.py"), timeout_seconds=10,
                      journal=self.journal, live_authorized=True)
        kwargs.update(changes)
        return live.CopilotProposer(**kwargs)

    def tool(self, name, arguments):
        tools = self.client.create_session.call_args.kwargs["tools"]
        return next(tool for tool in tools if tool.name == name).handler(ToolInvocation(arguments=arguments))

    async def turn(self, prompt, *, timeout):
        result = self.tool("read_source", {"path": "train.py"})
        current = json.loads(result.text_result_for_llm)
        self.assertEqual(self.tool("write_source", {"path": "train.py", "expected_sha256": current["sha256"],
                                                    "text": "RATE = 0.05\n"}).result_type, "success")
        self.assertEqual(self.tool("submit_plan", asdict(self.plan)).result_type, "success")

    async def test_fresh_session_structure_permissions_and_secret_filter(self):
        with patch.dict(os.environ, {"AZURE_CLIENT_SECRET": "not-for-agent"}):
            result = await self.adapter().run_iteration(self.context, self.repository)
        self.assertEqual(result, self.plan)
        self.assertNotIn("AZURE_CLIENT_SECRET", self.constructor.call_args.kwargs["env"])
        options = self.client.create_session.call_args.kwargs
        self.assertFalse(options["enable_config_discovery"])
        self.assertEqual(options["reasoning_summary"], "none")
        self.assertIsInstance(options["on_permission_request"](None, None), PermissionDecisionReject)
        self.client.stop.assert_awaited_once()
        self.session.abort.assert_not_awaited()
        self.assertEqual((self.repository / "train.py").read_bytes(), b"RATE = 0.05\n")
        self.assertEqual(self.journal.usage()["reserved"], 1)

    async def test_native_credit_limit_is_reserved_before_session_and_retained_on_failure(self):
        credits = ServiceJournal(self.root, service="copilot_credits", unit="microcredits", max_units=200000)
        async def fail_turn(*args, **kwargs):
            self.assertEqual(credits.usage()["reserved"], 200000)
            raise TimeoutError("Response lost")
        self.session.send_and_wait.side_effect = fail_turn
        with self.assertRaises(SpikeError):
            await self.adapter(credit_journal=credits, credits_per_turn=0.2).run_iteration(self.context, self.repository)
        self.assertEqual(self.client.create_session.call_args.kwargs["session_limits"], {"max_ai_credits": 0.2})
        self.assertEqual(credits.usage()["remaining"], 0)
        self.assertIsNone(credits.usage()["actual_usage"])
        with self.assertRaises(ServiceAdmissionError):
            await self.adapter(credit_journal=credits, credits_per_turn=0.2).run_iteration(
                replace(self.context, attempt_number=2), self.repository)
        self.client.create_session.assert_awaited_once()

    async def test_authentication_check_does_not_create_a_model_session(self):
        self.assertTrue(await live.check_authentication(self.root, self.runtime))
        self.client.create_session.assert_not_awaited()
        self.client.stop.assert_awaited_once()

    async def test_failed_attempt_is_never_replayed_and_simulation_is_not_live_evidence(self):
        self.client.start.side_effect = TimeoutError("ambiguous startup")
        with self.assertRaises(SpikeError):
            await self.adapter().run_iteration(self.context, self.repository)
        with self.assertRaisesRegex(SpikeError, "already admitted"):
            await self.adapter().run_iteration(self.context, self.repository)
        self.client.start.assert_awaited_once()
        self.assertEqual(self.journal.usage()["reserved"], 1)
        simulated = replace(self.context, research_state=replace(self.context.research_state, mode="simulated"))
        with self.assertRaises(SpikeError):
            await self.adapter().run_iteration(simulated, self.repository)

    async def test_invalid_calls_cannot_modify_protected_source_or_submit_wrong_parent(self):
        async def bad_turn(prompt, *, timeout):
            for name, args in (("read_source", {"path": "../secret"}),
                               ("write_source", {"path": "evaluate.py", "expected_sha256": "x", "text": "bad"}),
                               ("submit_plan", {**asdict(self.plan), "parent_experiment": "EXP-099"}),
                               ("list_source", {"command": "shell"})):
                self.assertEqual(self.tool(name, args).result_type, "denied")
        self.session.send_and_wait.side_effect = bad_turn
        with self.assertRaisesRegex(SpikeError, "structured"):
            await self.adapter().run_iteration(self.context, self.repository)
        self.session.abort.assert_awaited_once()
        self.client.stop.assert_awaited_once()

    async def test_unauthenticated_and_unauthorized_never_send_prompt(self):
        with self.assertRaises(SpikeError):
            self.adapter(live_authorized=False)
        self.constructor.assert_not_called()
        self.client.get_auth_status.return_value = GetAuthStatusResponse(isAuthenticated=False)
        with self.assertRaisesRegex(SpikeError, "authenticated"):
            await self.adapter().run_iteration(self.context, self.repository)
        self.client.create_session.assert_not_awaited()
        self.client.stop.assert_awaited_once()

    async def test_cancellation_aborts_and_closes_the_session(self):
        active = asyncio.Event()
        async def wait(*args, **kwargs):
            active.set()
            await asyncio.Event().wait()
        self.session.send_and_wait.side_effect = wait
        task = asyncio.create_task(self.adapter().run_iteration(self.context, self.repository))
        await asyncio.wait_for(active.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.session.abort.assert_awaited_once()
        self.client.stop.assert_awaited_once()


class ReadinessTests(unittest.TestCase):
    def test_saved_drafts_and_claimed_success_cannot_enable_live_execution(self):
        project = {"status": "contract_valid", "workspace_prepared": True, "evaluation_status": "confirmed",
                   "budget": {"max_experiments": 3, "max_gpu_hours": 1, "max_ai_credits": 1},
                   "accepted_exp_000": True, "execution_available": True}
        result = project_readiness(project)
        self.assertFalse(result["can_start"])
        codes = {item["code"] for item in result["blockers"]}
        self.assertIn("MEASURED_BASELINE", codes)
        self.assertIn("TRUSTED_SCORING", codes)
        self.assertIn("LIVE_CONTROLLER", codes)
        self.assertNotIn("EVALUATION_CONFIRMED", codes)

    def test_missing_or_zero_allowance_is_not_ready(self):
        for budget in ({}, {"max_experiments": 0, "max_gpu_hours": 0, "max_ai_credits": 0}):
            result = project_readiness({"budget": budget})
            codes = {item["code"] for item in result["blockers"]}
            self.assertTrue({"EXPERIMENT_LIMIT", "GPU_ALLOWANCE", "AI_ALLOWANCE"}.issubset(codes))


if __name__ == "__main__":
    unittest.main()
