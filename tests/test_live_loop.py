"""Real controller/Git/scoring/SQLite; fake remote compute and coding only."""
import asyncio
import copy
import hashlib
import io
import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock

import yaml

from research_intern.contracts.loader import load_contract
from research_intern.domain.experiments import JobRequest, SliceError
from research_intern.domain.research import CandidatePlan
from research_intern.execution.azure import AzureExecutor, AzureMLGateway, RemoteJob
from research_intern.execution.simulated import write_outputs
from research_intern.execution.outputs import OutputError, read_json
from research_intern.ledger.sqlite import Ledger, LedgerError
from research_intern.live import LiveProject, validate_settings
from research_intern.controller.mission import MissionControl
from research_intern.workspace.project import ProjectStore, SourceFile
from test_service_adapters import ServiceFixture, FakeAzure

SCORER = '''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--outputs');p.add_argument('--request');p.add_argument('--result')
a=p.parse_args(); request=json.loads(Path(a.request).read_text())
predictions=json.loads((Path(a.outputs)/'artifacts/predictions.json').read_text())
metric=sum(predictions)/len(predictions)
identity={k:request[k] for k in ('experiment_id','parent_experiment','git_commit','job_id','evaluation_fingerprint')}
Path(a.result).write_text(json.dumps({'identity':identity,'metrics':{'f1':metric}}))
'''


class MeasuredGateway(FakeAzure):
    def submit(self, name, code, payload):
        job = super().submit(name, code, payload)
        self.jobs[name] = replace(job, status="Completed")
        return self.jobs[name]

    def download(self, name, destination):
        payload = next(payload for key, _, payload in self.submissions if key == name)
        request = JobRequest(**payload["request"])
        contract = load_contract(next(code for key,code,_ in self.submissions if key == name) / "source")
        output = destination / "named-outputs/experiment_outputs"
        output.parent.mkdir(parents=True)
        write_outputs(output, contract.rules, request, score=0.999)
        run = read_json(output / "run.json")
        run.update(source_commit=request.git_commit, evaluation_fingerprint=contract.evaluation_fingerprint)
        (output / "run.json").write_text(json.dumps(run))
        score = [0.5, 0.8, 0.6, 0.7][int(request.experiment_id[4:])]
        (output / "artifacts/predictions.json").write_text(json.dumps([score,score]))
        return output


class Editor:
    backend = "copilot"
    def __init__(self, calls): self.calls = calls
    async def run_iteration(self, context, working_directory):
        self.calls.append(context)
        (working_directory / "train.py").write_text(f"RATE = {context.attempt_number / 100}\n")
        return CandidatePlan(context.selected_parent.experiment_id, "Measured evidence", "Test rate",
                             "Rate may improve score", "Change learning rate", "Measure independently")


class LiveLoopTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        (self.repository / "evaluate.py").write_text(SCORER)
        contract_path = self.repository / ".research_intern/contract.yaml"
        value = yaml.safe_load(contract_path.read_text())
        value["evaluation"]["evaluator_sha256"] = hashlib.sha256(SCORER.encode()).hexdigest()
        contract_path.write_text(yaml.safe_dump(value))
        store = ProjectStore(self.root)
        sources = [SourceFile("project/" + p.relative_to(self.repository).as_posix(), io.BytesIO(p.read_bytes()))
                   for p in self.repository.rglob("*") if p.is_file()]
        project = store.import_folder(sources)
        project = store.prepare(project["contract_sha256"])
        store.confirm_evaluation(project["contract_sha256"], project["source_commit"], project["evaluation_fingerprint"])
        (self.root / "fake-runtime").write_text("Not executed")
        self.settings_doc = {"azure": asdict(self.settings),
            "copilot": {"runtime_path": "fake-runtime", "model": "fake-model", "readable_paths": ["train.py"],
                        "max_turns": 3, "credits_per_turn": 0.2, "timeout_seconds": 10},
            "scoring": {"python": sys.executable, "files": ["evaluate.py", "split.json"], "timeout_seconds": 10}}
        self.live = LiveProject(self.root)
        self.live.configure(self.settings_doc, authorized=True)
        self.live.initialize()
        self.gateway = MeasuredGateway()
        self.calls = []

    def run_live(self, **kwargs):
        return asyncio.run(self.live.run(gateway=self.gateway, proposer_factory=lambda: Editor(self.calls), emit=lambda _: None, **kwargs))

    def test_baseline_then_repeated_research_uses_independent_scores_and_retains_rejection(self):
        baseline = self.run_live()
        self.assertEqual(baseline["research_state"]["baseline"]["score"], 0.5)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.live.inspect()["baseline_accepted"])
        self.live.activate()
        result = self.run_live()
        self.assertEqual(result["mode"], "live")
        self.assertEqual(result["research_state"]["best_experiment"]["score"], 0.8)
        self.assertEqual([c.selected_parent.experiment_id for c in self.calls], ["EXP-000", "EXP-001", "EXP-001"])
        self.assertEqual(result["research_state"]["last_experiment"]["decision"], "REJECT")
        with Ledger.reopen(self.live.root) as ledger:
            self.assertEqual(len(ledger.history()), 4)
            self.assertTrue(all(r.backend == "azure_ml" for r in ledger.history()))
            self.assertTrue((ledger.directory("EXP-001") / "verified-score.json").is_file())
        self.assertEqual(len(self.gateway.submissions), 4)
        report = MissionControl(self.root).report("live-project")
        self.assertEqual(report["provenance"]["evaluation_fingerprint"], self.live.configuration()["evaluation_fingerprint"])
        self.assertEqual(report["experiments"][1]["verified_score"]["metrics"], {"f1": 0.8})
        self.assertEqual(report["experiments"][1]["job_id"], self.gateway.submissions[1][0])
        self.assertFalse(MissionControl(self.root).dashboard()["can_start"])
        with self.assertRaises(SliceError): self.live.activate()

    def test_ambiguous_baseline_submission_reconciles_after_restart_without_duplicate(self):
        self.gateway.interrupt = True
        with self.assertRaises(TimeoutError):
            self.run_live()
        self.gateway.interrupt = False
        for name, job in self.gateway.jobs.items():
            self.gateway.jobs[name] = replace(job, status="Completed")
        self.live = LiveProject(self.root)
        self.run_live()
        self.assertEqual(len(self.gateway.submissions), 1)
        self.assertTrue(self.live.inspect()["baseline_accepted"])

    def test_live_limits_stop_before_coding_and_do_not_change_after_restart(self):
        self.run_live()
        self.live.activate()
        azure, turns, credits = self.live.journals(self.live.configuration())
        credits.reserve("exhaust", {}, units=credits.max_units)
        result = self.run_live()
        self.assertIn("AI_CREDIT_BUDGET_EXHAUSTED", result["controller"]["message"])
        self.assertFalse(self.calls)
        changed = copy.deepcopy(self.settings_doc)
        changed["copilot"]["max_turns"] = 9
        with self.assertRaises(SliceError): self.live.configure(changed, authorized=True)

    def test_frozen_scorer_tampering_blocks_before_remote_submission(self):
        (self.live.root / "trusted-scorer/evaluate.py").write_text("raise SystemExit(0)")
        with self.assertRaises(OutputError): self.run_live()
        self.assertFalse(self.gateway.submissions)

    def test_failed_independent_scoring_cannot_accept_a_claimed_good_baseline(self):
        download = self.gateway.download
        def bad_predictions(name, destination):
            output = download(name, destination)
            (output / "artifacts/predictions.json").write_text("[]")
            return output
        self.gateway.download = bad_predictions
        self.run_live()
        self.assertFalse(self.live.inspect()["baseline_accepted"])
        with Ledger.reopen(self.live.root) as ledger:
            self.assertEqual(ledger.get("EXP-000").failure_type, "OUTPUT_INVALID")
        self.assertFalse(self.calls)

    def test_nested_output_layout_preserves_receipt_and_detects_evidence_changes(self):
        from research_intern.evaluation.trusted import TrustedScorer
        self.run_live()
        with Ledger.reopen(self.live.root) as ledger:
            record = ledger.get("EXP-000")
            contract = replace(ledger.contract, outputs=replace(ledger.contract.outputs, root="outputs/results/"))
            nested = ledger.directory("EXP-000") / "outputs/results"
            nested.parent.mkdir()
            ledger.outputs("EXP-000").rename(nested)
        config = self.live.configuration()
        scorer = TrustedScorer(self.live.root, contract, python=sys.executable,
                               files=config["scorer_files"], timeout_seconds=10)
        request = JobRequest(record.experiment_id, None, record.git_commit)
        self.assertEqual(scorer.verify(nested, request, record.job_id).score, 0.5)
        (nested / "artifacts/predictions.json").write_text("[0.99]")
        with self.assertRaisesRegex(OutputError, "evidence changed"):
            scorer.verify(nested, request, record.job_id)

    def test_live_and_simulated_ledgers_cannot_be_relabelled(self):
        with self.assertRaises(LedgerError):
            Ledger(self.live.root, self.contract.rules)

    def test_service_verification_is_read_only_and_expires(self):
        gateway = Mock()
        gateway.preflight.return_value = {"gpus": 1, "image": self.settings.image}
        with patch("research_intern.live.AzureMLGateway", return_value=gateway), patch(
                "research_intern.copilot.live.check_authentication", new_callable=AsyncMock) as auth:
            proof = asyncio.run(self.live.verify_services())
        gateway.submit.assert_not_called()
        gateway.close.assert_called_once()
        auth.assert_awaited_once()
        self.assertTrue(self.live.inspect()["services_verified"])
        proof["checked_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        (self.live.root / "services-verified.json").write_text(json.dumps(proof))
        self.assertFalse(self.live.inspect()["services_verified"])

    def test_invalid_nested_settings_fail_as_domain_errors(self):
        for key in ("azure", "copilot", "scoring"):
            invalid = copy.deepcopy(self.settings_doc)
            invalid[key] = []
            with self.subTest(key=key), self.assertRaises(SliceError):
                validate_settings(invalid, self.root)
        invalid = copy.deepcopy(self.settings_doc)
        invalid["azure"]["gpu_count"] = "one"
        with self.assertRaises(SliceError): validate_settings(invalid, self.root)

    def test_http_live_state_report_and_setup_errors(self):
        from fastapi.testclient import TestClient
        from research_intern.api.app import create_app
        with TestClient(create_app(self.root), base_url="http://localhost") as client:
            self.assertEqual(client.get("/api/health").json()["mode"], "live")
            response = client.get("/api/runs/live-project/report")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["report_version"], 1)
            response = client.post("/api/project/live", headers={"x-research-intern": "1"},
                json={"settings": {"azure": [], "copilot": {}, "scoring": {}}, "authorized": True})
            self.assertEqual(response.status_code, 409)
            response = client.post("/api/project/baseline", headers={"x-research-intern": "1"})
            self.assertEqual(response.status_code, 409)
            self.assertFalse(self.gateway.submissions)

    def test_azure_preflight_checks_gpu_and_asset_types_without_submission(self):
        from research_intern.execution.adapter import ExecutionError
        gateway = AzureMLGateway.__new__(AzureMLGateway)
        gateway.settings = self.settings
        gateway.client = Mock()
        gateway.client.environments.get.return_value = Mock(image=self.settings.image, conda_file=None, build=None)
        gateway.client.compute.get.return_value = Mock(provisioning_state="Succeeded", location="fixture", size="gpu-1")
        size = Mock(gpus=1)
        size.name = "gpu-1"
        gateway.client.compute.list_sizes.return_value = [size]
        gateway.client.data.get.side_effect = lambda name, version: Mock(type="uri_folder" if name == "data" else "uri_file")
        self.assertEqual(gateway.preflight()["gpus"], 1)
        from enum import Enum
        class ProvisioningState(str, Enum):
            SUCCEEDED = "Succeeded"
            CREATING = "Creating"
        gateway.client.compute.get.return_value.provisioning_state = ProvisioningState.SUCCEEDED
        self.assertEqual(gateway.preflight()["gpus"], 1)
        gateway.client.compute.get.return_value.provisioning_state = ProvisioningState.CREATING
        with self.assertRaises(ExecutionError): gateway.preflight()
        gateway.client.compute.get.return_value.provisioning_state = ProvisioningState.SUCCEEDED
        size.gpus = 2
        with self.assertRaises(ExecutionError): gateway.preflight()
        gateway.client.jobs.create_or_update.assert_not_called()

    def test_new_gateway_constructs_valid_generic_azure_command_without_network(self):
        from unittest.mock import Mock
        from azure.ai.ml import command
        gateway = AzureMLGateway.__new__(AzureMLGateway)
        gateway.settings = replace(self.settings, inputs={"config": {"baseline":"configs/baseline.yaml", "candidate":"configs/candidate.yaml"}})
        gateway.client = Mock()
        gateway.client.environments.get.return_value = Mock(image=self.settings.image, conda_file=None, build=None)
        gateway.client.jobs.create_or_update.side_effect = lambda job: job
        request = JobRequest("EXP-001", "EXP-000", "a"*40)
        payload = {"settings":asdict(gateway.settings),"request":asdict(request),"command":"python train.py --config ${{inputs.config}}", "environment_variables":{"PYTHONPATH":"src"}}
        gateway.submit("ri-azure-"+"a"*32, self.repository, payload)
        job = gateway.client.jobs.create_or_update.call_args.args[0]
        self.assertEqual(job._to_dict()["inputs"]["config"], "configs/candidate.yaml")
        self.assertTrue(job._validate().passed)
