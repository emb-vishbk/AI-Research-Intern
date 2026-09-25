"""Ordinary source import through preparation, baseline reuse and one candidate."""
import asyncio
from contextlib import nullcontext
import io
import json
from pathlib import Path
import stat
import subprocess
import shlex
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

import yaml

from research_intern.api.app import create_app
from research_intern.connections import Connections
from research_intern.controller.mission import MissionControl
from research_intern.domain.experiments import SliceError
from research_intern.execution.azure import RemoteJob
from research_intern.execution.discovery import AzureDiscovery, DiscoveryError, selection
from research_intern.execution.native import submit_native, validate_native
from research_intern.onboarding import Onboarding
from research_intern.workspace.discovery import ProjectImporter, inspect_project
from research_intern.workspace.project import SourceFile
from research_intern.workspace.setup import prepare_discovered
from research_intern.workspace.importing import contract_digest, prepared_workspace
from test_execution_slice import WorkspaceTest
from test_live_loop import Editor

TARGET = {"subscription_id": "12345678-1234-1234-1234-123456789abc", "resource_group": "my-group",
          "workspace_name": "my-workspace", "compute": "gpu-one"}
EVALUATOR = '''import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--predictions');p.add_argument('--reference');p.add_argument('--output')
a=p.parse_args(); values=json.loads(Path(a.predictions).read_text()); ref=json.loads(Path(a.reference).read_text())
Path(a.output).write_text(json.dumps({'f1':sum(values)/len(values)/ref['scale']}))
'''
JOB = {"code": ".", "command": "python train.py --output ${{outputs.result}}", "experiment_name": "pedestrians",
       "environment": "azureml:my-env:3", "compute": "azureml:gpu-one",
       "inputs": {"weights": {"type": "uri_file", "path": "./weights/model.pt"}},
       "outputs": {"result": {"type": "uri_folder"}}}


def source_files(extra=None):
    data = {"train.py": b"RATE = 0.1\n", "evaluate.py": EVALUATOR.encode(), "split.json": b'{"scale":1}',
            "job.yaml": yaml.safe_dump(JOB).encode(), "weights/model.pt": b"fixture-checkpoint",
            "outputs/metrics.json": b'{"f1":0.999}', ".gitignore": b"split.json\n.research_intern/\n",
            ".git/config": b"never import", ".venv/secrets.txt": b"never import", ".env": b"PRIVATE=never import"}
    data.update(extra or {})
    return [SourceFile("ordinary/" + k, io.BytesIO(v)) for k, v in data.items()]


class Cloud:
    def __init__(self): self.status, self.downloads, self.reads = "Running", 0, []
    def validate(self, target): return {"gpu_count": 1, "provisioning_state": "Succeeded", "compute_size": "T4"}
    def job(self, target, name):
        self.reads.append((dict(target), name))
        return {"name": name, "status": self.status, "experiment_name": "pedestrians", "compute": "gpu-one"}
    def jobs(self, target, experiment_name, job_name): return {"items": [self.job(target, job_name or "old-run")], "truncated": False}
    def download(self, target, name, destination):
        self.downloads += 1
        output = destination / "named-outputs/result"
        output.mkdir(parents=True)
        (output / "predictions.json").write_text("[0.5,0.5]")
        (output / "metrics.json").write_text('{"f1":0.999}')


class NativeGateway:
    def __init__(self): self.submissions, self.jobs = [], {}
    def submit(self, name, code, payload):
        self.submissions.append((name, code, payload))
        self.jobs[name] = RemoteJob(name, "Completed", __import__('research_intern.execution.azure', fromlist=['AzureExecutor']).AzureExecutor._tags(payload))
        return self.jobs[name]
    def get(self, name): return self.jobs.get(name)
    def download(self, name, destination):
        payload = next(p for n, _, p in self.submissions if n == name)
        output = destination / "results"
        output.mkdir()
        r = payload["request"]
        (output / "run.json").write_text(json.dumps({"experiment_id": r["experiment_id"], "parent_experiment": r["parent_experiment"],
            "source_commit": r["git_commit"], "evaluation_fingerprint": payload["evaluation_fingerprint"], "status": "completed"}))
        (output / "predictions.json").write_text("[0.8,0.8]" if r["parent_experiment"] else "[0.5,0.5]")
        return output


class ImportTests(WorkspaceTest):
    def test_ordinary_folder_filters_private_files_and_retains_inputs(self):
        importer = ProjectImporter(self.root)
        result = importer.import_files(source_files())
        self.assertEqual(result["training"], ["train.py"])
        self.assertEqual(result["metrics"][0]["values"]["f1"], .999)
        self.assertEqual(result["jobs"][0]["code_root"], ".")
        self.assertFalse((importer.root / "repository/.env").exists())
        self.assertFalse((importer.root / "repository/.git").exists())
        self.assertTrue((importer.root / "assets/weights/model.pt").exists())
        self.assertFalse((importer.root / "repository/.research_intern/contract.yaml").exists())

    def test_zip_accepts_normal_repository_with_git_and_rejects_traversal(self):
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            for item in source_files(): archive.writestr(item.path, item.stream.read())
        bundle.seek(0)
        result = ProjectImporter(self.root).import_files([SourceFile("project.zip", bundle)], archive=True)
        self.assertIn("train.py", result["files"])
        self.assertNotIn(".git/config", result["files"])

    def test_zip_unsafe_paths_and_links_never_publish(self):
        for bad in ("../escape.py", "/absolute.py", "project/.git/../../escape.py", "project/a\\b.py"):
            with self.subTest(path=bad):
                bundle = io.BytesIO()
                with zipfile.ZipFile(bundle, "w") as archive: archive.writestr(bad, "pass")
                bundle.seek(0)
                with self.assertRaises(SliceError):
                    ProjectImporter(self.root).import_files([SourceFile("project.zip", bundle)], archive=True)
                self.assertFalse((self.root / ".runtime/research-project/repository").exists())
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            entry = zipfile.ZipInfo("project/link"); entry.create_system = 3; entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../escape")
        bundle.seek(0)
        with self.assertRaises(SliceError): ProjectImporter(self.root).import_files([SourceFile("project.zip", bundle)], archive=True)

    def test_duplicate_case_and_size_limits(self):
        with self.assertRaises(SliceError):
            ProjectImporter(self.root).import_files([SourceFile("no-folder.py", io.BytesIO(b"pass"))])
        with self.assertRaises(SliceError): ProjectImporter(self.root).import_files(source_files({"Train.py": b"pass"}))
        with patch("research_intern.workspace.discovery.MAX_PROJECT_BYTES", 2):
            with self.assertRaises(SliceError): ProjectImporter(self.root).import_files(source_files())
        self.assertFalse((self.root / ".runtime/research-project/repository").exists())


class OnboardingFixture(WorkspaceTest):
    def setUp(self):
        super().setUp()
        (self.root / "fake-runtime").write_text("not executed")
        self.mission = MissionControl(self.root)
        providers = SimpleNamespace(settings=lambda: (None, None, False), bind=Mock())
        self.connections = SimpleNamespace(providers=providers, snapshot=lambda: {
            "azure": {"status": "connected"}, "copilot": {"status": "connected", "selected_model": "fixture-model", "runtime_path": "fake-runtime"}})
        self.cloud = Cloud()
        self.onboarding = Onboarding(self.root, self.mission, self.connections, cloud=self.cloud)
        self.addCleanup(self.onboarding.close)
        self.onboarding.upload(source_files())
        self.onboarding.validate_target(TARGET)
        self.choices = {"goal": "Improve f1 with a fixed reference and limited compute", "job_config": "job.yaml",
            "metric": "f1", "direction": "maximize", "evaluation_file": "evaluate.py", "validation_file": "split.json",
            "evaluation_command": "python evaluate.py --predictions {outputs}/predictions.json --reference {reference} --output {result}",
            "evaluation_metrics": "fresh-metrics.json", "editable": ["train.py"], "constraints": {},
            "budget": {"max_experiments": 1, "max_gpu_hours": 1.0, "max_ai_credits": 100.0}, "job_timeout_seconds": 60,
            "scoring_python": sys.executable}
        self.onboarding.save(self.choices)

    def select_existing(self):
        with patch.object(self.onboarding, "start"):
            self.onboarding.choose_job("old-run")

    def prepare(self, existing=False):
        if existing:
            self.select_existing()
            self.cloud.status = "Completed"
            self.onboarding.poll_once()
            self.onboarding.save({**self.choices, "existing_compatible": True})
        else:
            self.onboarding.find_jobs(experiment_name="pedestrians")
            self.onboarding.new_baseline()
        prepare_discovered(self.onboarding, authorized=True)
        return self.mission.live

    def prepare_legacy_source(self):
        repository = self.onboarding.root / "repository"
        internal = repository / ".research_intern"
        internal.mkdir()
        legacy = {"version": "1.0", "objective": {"metric": "f1", "direction": "maximize"},
                  "execution": {"backend": "azure_ml", "job_config": "job.yaml"},
                  "outputs": {"root": "experiment_outputs/"},
                  "scope": {"editable": ["train.py"], "protected": ["evaluate.py", "split.json"]},
                  "constraints": {}, "budget": {"max_experiments": 0}}
        (internal / "contract.yaml").write_text(yaml.safe_dump(legacy))
        (repository / ".gitignore").write_text("")
        self.mission.projects.prepare(contract_digest(repository))
        self.onboarding.find_jobs(); self.onboarding.new_baseline()
        return prepared_workspace(self.onboarding.root, repository)

class OnboardingTests(OnboardingFixture):
    def test_legacy_preparation_uses_reviewed_limits_and_archives_original(self):
        original = self.prepare_legacy_source()
        budget = {"max_experiments": 3, "max_gpu_hours": 2.0, "max_ai_credits": 100.0}
        self.onboarding.save({**self.choices, "budget": budget})
        prepare_discovered(self.onboarding, authorized=True)
        root = self.onboarding.root
        contract = yaml.safe_load((root / "repository/.research_intern/contract.yaml").read_text())
        self.assertEqual(contract["budget"], budget)
        self.assertTrue(contract["execution"]["native"])
        self.assertEqual(contract["evaluation"]["evaluator"], ".research_intern/score_runner.py")
        self.assertEqual(self.mission.projects.inspect()["budget"], budget)
        history = list((root / "preparation-history").iterdir())
        self.assertEqual(len(history), 1)
        previous = history[0] / "previous"
        self.assertEqual(prepared_workspace(previous, previous / "repository"), original)
        self.assertFalse((root / "ledger.sqlite3").exists())
        live_before = (root / "live.json").read_bytes()
        with self.assertRaisesRegex(SliceError, "setup is fixed"):
            self.onboarding.save({**self.choices, "budget": {**budget, "max_experiments": 4}})
        self.assertEqual((root / "live.json").read_bytes(), live_before)

    def test_invalid_revised_evaluator_leaves_legacy_preparation_untouched(self):
        original = self.prepare_legacy_source()
        self.onboarding.save({**self.choices, "evaluation_file": "train.py", "editable": ["evaluate.py"],
                              "evaluation_command": ""})
        with self.assertRaisesRegex(SliceError, "selected evaluator.*train.py"):
            prepare_discovered(self.onboarding, authorized=True)
        root = self.onboarding.root
        self.assertEqual(prepared_workspace(root, root / "repository"), original)
        self.assertFalse((root / "preparation-history").exists())
        self.assertFalse((root / "live.json").exists())

    def test_preparation_revision_recovers_interrupted_directory_publication(self):
        original = self.prepare_legacy_source()
        root = self.onboarding.root
        rename = Path.rename
        def fail_publish(path, destination):
            if path.name == "repository" and path.parent.name == "next":
                raise OSError("interrupted publication")
            return rename(path, destination)
        with patch.object(Path, "rename", fail_publish):
            with self.assertRaisesRegex(OSError, "interrupted publication"):
                prepare_discovered(self.onboarding, authorized=True)
        self.assertTrue((root / "preparation.pending.json").exists())
        self.assertFalse((root / "live.json").exists())
        prepare_discovered(self.onboarding, authorized=True)
        self.assertTrue(self.mission.live.inspect()["configured"])
        self.assertFalse((root / "preparation.pending.json").exists())
        history = list((root / "preparation-history").iterdir())
        self.assertEqual(len(history), 1)
        previous = history[0] / "previous"
        self.assertEqual(prepared_workspace(previous, previous / "repository"), original)

    def test_revision_refuses_dirty_source_or_initialized_research(self):
        original = self.prepare_legacy_source()
        root = self.onboarding.root
        training = root / "repository/train.py"
        before = training.read_bytes()
        training.write_bytes(b"USER_EDIT = True\n")
        with self.assertRaisesRegex(SliceError, "uncommitted"):
            prepare_discovered(self.onboarding, authorized=True)
        self.assertEqual(training.read_bytes(), b"USER_EDIT = True\n")
        training.write_bytes(before)
        (root / "ledger.sqlite3").write_bytes(b"existing research")
        with self.assertRaisesRegex(SliceError, "initialized"):
            prepare_discovered(self.onboarding, authorized=True)
        self.assertEqual(prepared_workspace(root, root / "repository"), original)
        self.assertFalse((root / "preparation-history").exists())

    def test_wait_download_restart_without_copilot_or_submission(self):
        self.select_existing()
        self.onboarding.poll_once()
        self.assertEqual(self.onboarding.snapshot()["existing_run"]["phase"], "waiting")
        self.assertEqual(self.cloud.downloads, 0)
        restarted = Onboarding(self.root, self.mission, self.connections, cloud=self.cloud)
        self.addCleanup(restarted.close)
        self.cloud.status = "Completed"
        restarted.poll_once(); restarted.poll_once()
        self.assertEqual(self.cloud.downloads, 1)
        self.assertEqual(restarted.snapshot()["existing_run"]["metrics"][0]["values"]["f1"], .999)
        self.assertFalse((self.onboarding.root / "ledger.sqlite3").exists())

    def test_native_baseline_and_candidate_use_original_yaml_and_frozen_evaluator(self):
        live = self.prepare()
        live.initialize()
        gateway, calls = NativeGateway(), []
        asyncio.run(live.run(gateway=gateway, proposer_factory=lambda: Editor(calls), emit=lambda _: None))
        self.assertEqual(calls, [])
        self.assertTrue(live.inspect()["baseline_accepted"])
        live.activate()
        result = asyncio.run(live.run(gateway=gateway, proposer_factory=lambda: Editor(calls), emit=lambda _: None))
        self.assertEqual(result["research_state"]["best_experiment"]["score"], .8)
        self.assertEqual(len(gateway.submissions), 2)
        self.assertEqual(len(calls), 1)
        _, code, payload = gateway.submissions[1]
        self.assertEqual((code / "source/weights/model.pt").read_bytes(), b"fixture-checkpoint")
        self.assertEqual(yaml.safe_load((code / "source/job.yaml").read_text()), JOB)
        client = SimpleNamespace(jobs=SimpleNamespace(create_or_update=lambda job: job))
        job = submit_native(client, "fixture", code, payload, {"ri_commit": "fixture"})
        self.assertEqual(job.environment.removeprefix("azureml:"), JOB["environment"].removeprefix("azureml:"))
        self.assertIn("${{outputs.result}}", job.command)
        self.assertIn("ri_results", job.outputs)
        self.assertEqual(job.compute, "gpu-one")
        self.assertEqual(job.limits.timeout, 60)

    def test_existing_baseline_is_rescored_then_only_candidate_submitted(self):
        live = self.prepare(existing=True)
        live.initialize()
        gateway, calls = NativeGateway(), []
        result = asyncio.run(live.run(gateway=gateway, proposer_factory=lambda: Editor(calls), emit=lambda _: None))
        self.assertEqual(result["research_state"]["baseline"]["score"], .5)
        self.assertEqual(gateway.submissions, [])
        self.assertEqual(live.inspect()["usage"]["azure"]["reserved"], 0)
        self.assertEqual(calls, [])
        receipt = json.loads((live.root / "imported-baseline.json").read_text())
        self.assertEqual(receipt["source_association"], "user_attested")
        live.activate()
        asyncio.run(live.run(gateway=gateway, proposer_factory=lambda: Editor(calls), emit=lambda _: None))
        self.assertEqual(len(gateway.submissions), 1)
        self.assertEqual(gateway.submissions[0][2]["request"]["experiment_id"], "EXP-001")

    def test_running_existing_run_cannot_trigger_duplicate_baseline(self):
        self.select_existing()
        self.onboarding.save({**self.choices, "existing_compatible": True})
        prepare_discovered(self.onboarding, authorized=True)
        with self.assertRaisesRegex(SliceError, "Wait for"):
            self.mission.live.initialize()
        self.assertFalse((self.onboarding.root / "ledger.sqlite3").exists())

    def test_no_guessing_scope_or_baseline_and_no_partial_setup_on_invalid_input(self):
        with self.assertRaisesRegex(SliceError, "three budget fields"):
            self.onboarding.save({**self.choices, "budget": {}})
        with self.assertRaisesRegex(SliceError, "Find existing"):
            prepare_discovered(self.onboarding, authorized=True)
        self.onboarding.find_jobs(); self.onboarding.new_baseline()
        self.onboarding.save({**self.choices, "editable": ["train.py", "evaluate.py"]})
        with self.assertRaisesRegex(SliceError, "remain fixed"):
            prepare_discovered(self.onboarding, authorized=True)
        self.assertFalse((self.onboarding.root / "repository/.research_intern").exists())

    def test_changed_asset_is_blocked_before_any_submission(self):
        live = self.prepare(); live.initialize()
        (live.root / "assets/weights/model.pt").write_bytes(b"changed")
        gateway = NativeGateway()
        with self.assertRaisesRegex(SliceError, "Retained input changed"):
            asyncio.run(live.run(gateway=gateway, proposer_factory=lambda: Editor([]), emit=lambda _: None))
        self.assertEqual(gateway.submissions, [])

    def test_preparation_can_resume_after_settings_publication_failure(self):
        self.onboarding.find_jobs(); self.onboarding.new_baseline()
        with patch.object(self.mission.live, "configure", side_effect=SliceError("interrupted configuration")):
            with self.assertRaisesRegex(SliceError, "interrupted configuration"):
                prepare_discovered(self.onboarding, authorized=True)
        self.assertTrue((self.onboarding.root / "workspace.json").is_file())
        prepare_discovered(self.onboarding, authorized=True)
        self.assertTrue(self.mission.live.inspect()["configured"])
        self.assertFalse((self.onboarding.root / "ledger.sqlite3").exists())

    def test_draft_budget_can_change_after_configuration_failure(self):
        self.onboarding.find_jobs(); self.onboarding.new_baseline()
        with patch.object(self.mission.live, "configure", side_effect=SliceError("interrupted configuration")):
            with self.assertRaisesRegex(SliceError, "interrupted configuration"):
                prepare_discovered(self.onboarding, authorized=True)
        self.onboarding.save({**self.choices, "budget": {**self.choices["budget"], "max_experiments": 3}})
        prepare_discovered(self.onboarding, authorized=True)
        self.assertEqual(self.mission.projects.inspect()["budget"]["max_experiments"], 3)
        self.assertEqual(len(list((self.onboarding.root / "preparation-history").iterdir())), 1)

    def test_changed_target_during_poll_does_not_publish_old_job(self):
        self.select_existing()
        original = self.cloud.job
        def changed(target, name):
            with self.onboarding._guard:
                state = self.onboarding._read(); state["existing_run"] = None; self.onboarding._write(state)
            return original(target, name)
        self.cloud.job = changed
        self.onboarding.poll_once()
        self.assertIsNone(self.onboarding.snapshot()["existing_run"])

    def test_api_folder_zip_selection_and_manual_resource_fallback(self):
        from fastapi.testclient import TestClient
        for archived in (False, True):
            workspace = self.root / ("zip-app" if archived else "folder-app"); workspace.mkdir()
            mission = MissionControl(workspace)
            service = Onboarding(workspace, mission, self.connections, cloud=self.cloud)
            client = TestClient(create_app(workspace, mission=mission, connections=self.connections, onboarding=service), base_url="http://127.0.0.1")
            self.addCleanup(client.close)
            headers = {"X-Research-Intern": "1"}
            if archived:
                data = io.BytesIO()
                with zipfile.ZipFile(data, "w") as archive:
                    for item in source_files(): archive.writestr(item.path, item.stream.read())
                files = [("files", ("my-project.zip", data.getvalue()))]
            else:
                files = [("files", (item.path, item.stream.read())) for item in source_files()]
            response = client.post(f"/api/onboarding/upload?archive={str(archived).lower()}", files=files, headers=headers)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json()["job_config"], "job.yaml")
            response = client.post("/api/onboarding/target", json=TARGET, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["target_validated"])
            response = client.post("/api/onboarding/choices", json=self.choices, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            response = client.post("/api/onboarding/jobs", json={"experiment_name": "pedestrians"}, headers=headers)
            self.assertEqual(response.json()["items"][0]["name"], "old-run")
            self.assertEqual(client.get("/assets/onboarding.js").status_code, 200)
            self.assertEqual(client.get("/assets/onboarding.css").status_code, 200)
            page = client.get("/").text
            for old_control in ('id="live-settings-file"', 'id="configure-live"', 'id="configure-draft"', '>Live services<'):
                self.assertNotIn(old_control, page)
            self.assertEqual(client.post("/api/onboarding/target", json=TARGET).status_code, 403)

    def test_failed_existing_run_preserves_evidence_and_requires_new_choice(self):
        self.select_existing(); self.cloud.status = "Failed"; self.onboarding.poll_once()
        self.onboarding.save({**self.choices, "existing_compatible": True})
        with self.assertRaisesRegex(SliceError, "did not complete"):
            prepare_discovered(self.onboarding, authorized=True)
        self.assertEqual(self.onboarding.snapshot()["existing_run"]["phase"], "downloaded")
        self.assertFalse((self.onboarding.root / "live.json").exists())

    def test_job_wrapper_preserves_artifacts_without_inventing_metrics(self):
        if os.name == "nt": self.skipTest("Azure command wrapper uses the Linux job shell")
        from research_intern.workspace import job_runner
        work = self.root / "wrapper"; work.mkdir()
        original = work / "original"; original.mkdir()
        output = work / "collected"
        code = "from pathlib import Path; Path(" + repr(str(original / "predictions.json")) + ").write_text('[0.5]'); print('trained')"
        command = shlex.quote(sys.executable) + " -c " + shlex.quote(code)
        subprocess.run([sys.executable, str(Path(job_runner.__file__)), "--command", command, "--cwd", str(work),
            "--output", str(output), "--experiment-id", "EXP-000", "--parent", "none", "--commit", "a" * 40,
            "--fingerprint", "b" * 64, "--original-output", "result", str(original)], check=True, capture_output=True)
        self.assertEqual((output / "predictions.json").read_text(), "[0.5]")
        self.assertIn("trained", (output / "logs/training.log").read_text())
        self.assertEqual(json.loads((output / "run.json").read_text())["source_commit"], "a" * 40)
        self.assertFalse((output / "metrics.json").exists())


class AzureDiscoveryTests(WorkspaceTest):
    def test_resource_dropdowns_and_explicit_subscription_validation(self):
        cloud = AzureDiscovery(lambda: object())
        with patch.object(cloud, "_items", return_value=([{"subscriptionId": TARGET["subscription_id"], "displayName": "Research subscription"}], False)) as listing:
            result = cloud.resources("subscriptions", {})
            self.assertEqual(result["items"][0]["value"], TARGET["subscription_id"])
            self.assertEqual(result["items"][0]["label"], "Research subscription")
            self.assertIn("/subscriptions?", listing.call_args.args[0])
        with self.assertRaisesRegex(SliceError, "UUID"): selection({"subscription_id": "Research-subscription"})
        with self.assertRaisesRegex(SliceError, "first"): cloud.resources("workspaces", {})
        with self.assertRaisesRegex(DiscoveryError, "Sign in"): AzureDiscovery(lambda: None)._credential()

    def test_bad_pagination_host_never_receives_token(self):
        credential = Mock(); credential.get_token.return_value.token = "private"
        response = Mock(); response.json.return_value = {"value": [], "nextLink": "https://untrusted.example/next"}
        session = Mock(); session.get.return_value = response
        with patch("requests.Session", return_value=nullcontext(session)):
            with self.assertRaises(DiscoveryError): AzureDiscovery(lambda: credential)._items("/subscriptions")
        self.assertEqual(session.get.call_count, 1)

    def test_invalid_native_paths_and_multinode_fail_before_execution(self):
        repo = self.root / "repo"; repo.mkdir()
        for change in ({"code": ".."}, {"distribution": {"type": "pytorch"}}, {"outputs": {"result": {"path": "azureml://fixed"}}}):
            (repo / "job.yaml").write_text(yaml.safe_dump({**JOB, **change}))
            with self.assertRaises(SliceError): validate_native(repo, "job.yaml")
