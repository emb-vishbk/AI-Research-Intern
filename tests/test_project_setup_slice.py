"""Real local source setup and evaluation policy checks; no ML or service calls."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from research_intern.contracts.loader import load_contract
from research_intern.contracts.models import ExperimentContract
from research_intern.controller.candidate import CandidateController
from research_intern.controller.execution import ExecutionController
from research_intern.copilot.editing import ScriptedEditor
from research_intern.domain.experiments import JobRequest, SliceError
from research_intern.evaluation.evaluator import evaluate_baseline
from research_intern.execution.outputs import OutputError, collect_outputs
from research_intern.execution.simulated import SimulatedExecutor, write_outputs
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.git import GitWorkspace, PermissionViolation
from research_intern.workspace.project import PROJECT_PATH, ProjectStore, SourceFile
from test_execution_slice import WorkspaceTest


def sources(*, evaluation=True):
    files = {"train.py": b"raise RuntimeError('never execute imported code')\n",
             "evaluate.py": b"# Fixed evaluator entrypoint for fixture tests only\n",
             "split.json": b'{"validation": ["image-1", "image-2"]}\n',
             "azure_job.yaml": b"type: command\ncommand: python train.py\n"}
    contract = {"version": "1.0", "objective": {"metric": "bbox_score", "direction": "maximize"},
                "execution": {"backend": "azure_ml", "job_config": "azure_job.yaml"},
                "outputs": {"root": "experiment_outputs/"},
                "scope": {"editable": ["train.py"], "protected": []},
                "constraints": {"latency_ms": {"max": 50}}, "budget": {"max_experiments": 2}}
    if evaluation:
        contract["evaluation"] = {
            "metric_definition": "Fixture metric on a 0–1 scale; not a research recommendation.",
            "procedure": "Use the fixed evaluator and split; measure latency under the declared workload conditions.",
            "dataset_version": "fixture-data-v1",
            "evaluator": "evaluate.py", "evaluator_sha256": hashlib.sha256(files["evaluate.py"]).hexdigest(),
            "validation_split": "split.json", "validation_split_sha256": hashlib.sha256(files["split.json"]).hexdigest(),
        }
    files[".research_intern/contract.yaml"] = json.dumps(contract).encode()
    return files


class LocalSetupTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.store = ProjectStore(self.root)
        self.repository = self.root / PROJECT_PATH

    def upload(self, files=None):
        files = sources() if files is None else files
        return self.store.import_folder([SourceFile(f"Research/{name}", io.BytesIO(raw)) for name, raw in files.items()])

    def prepare(self, files=None):
        uploaded = self.upload(files)
        return self.store.prepare(uploaded["contract_sha256"])

    def confirm(self, project):
        return self.store.confirm_evaluation(project["contract_sha256"], project["source_commit"], project["evaluation_fingerprint"])

    def test_import_is_reproducible_setup_not_a_measured_baseline(self):
        before = sources()
        uploaded = self.upload(before)
        self.assertFalse(uploaded["workspace_prepared"])
        project = self.store.prepare(uploaded["contract_sha256"])
        self.assertTrue(project["workspace_prepared"], project)
        self.assertEqual(project["evaluation_status"], "unconfirmed")
        self.assertFalse(project["execution_available"])
        self.assertRegex(project["source_commit"], r"^[a-f0-9]{40}$")
        git = GitWorkspace(self.store.root(), self.repository)
        git.verify_clean(project["source_commit"])
        for name, raw in before.items():
            self.assertEqual((self.repository / name).read_bytes(), raw)
            self.assertEqual(git._git("show", f"{git.head}:{name}"), raw)
        self.assertEqual(git._git("rev-list", "--count", "HEAD").strip(), b"1")
        self.assertEqual(self.store.prepare(project["contract_sha256"])["source_commit"], git.head)
        self.assertFalse((self.store.root() / "ledger.sqlite3").exists())
        self.assertEqual(list(self.store.root().glob("prepare-*")), [])

    def test_unknown_evaluation_does_not_get_approved_or_invented(self):
        project = self.prepare(sources(evaluation=False))
        self.assertTrue(project["workspace_prepared"])
        self.assertEqual(project["evaluation_status"], "missing")
        with self.assertRaises(SliceError):
            self.store.confirm_evaluation(project["contract_sha256"], project["source_commit"], "a" * 64)
        self.assertFalse((self.store.root() / "evaluation.json").exists())

    def test_imported_workspace_uses_existing_controller_and_protocol_output_gate(self):
        project = self.prepare()
        contract = load_contract(self.repository)
        git = GitWorkspace(self.store.root(), self.repository)
        # This test alone supplies clearly synthetic measurements to exercise the
        # existing controller; the project preparation API never creates a ledger.
        with Ledger(self.store.root(), contract.rules, contract=contract, repository=self.repository) as ledger:
            executor = SimulatedExecutor(self.store.root(), contract.rules)
            execution = ExecutionController(ledger, executor)
            outputs = ledger.outputs("EXP-000")
            write_outputs(outputs, contract.rules, JobRequest("EXP-000", None, project["source_commit"]), score=0.5)
            run_path = outputs / "run.json"
            run = json.loads(run_path.read_text())
            with self.assertRaisesRegex(OutputError, "evaluation_fingerprint"):
                execution.import_baseline(project["source_commit"])
            self.assertEqual(ledger.history(), [])
            run["evaluation_fingerprint"] = contract.evaluation_fingerprint
            run_path.write_text(json.dumps(run))
            execution.import_baseline(project["source_commit"])
            candidate = CandidateController(ledger, git, lambda: ScriptedEditor(path="train.py"))
            record = asyncio.run(candidate.prepare_candidate())
            self.assertEqual(record.state, "PREPARED")
            self.assertNotEqual(record.git_commit, project["source_commit"])
            self.assertIn("train.py", record.diff)
            self.assertEqual(load_contract(self.repository), contract)
            execution.resume_submission(record.experiment_id)
            for _ in range(3):
                record = execution.advance(record.experiment_id)
                if record.terminal:
                    break
            # The unmodified mock executor does not claim to run this protocol.
            self.assertEqual(record.failure_type, "OUTPUT_INVALID")
            self.assertEqual(ledger.best().experiment_id, "EXP-000")

    def test_confirmation_is_explicit_persistent_and_bound_to_exact_source(self):
        project = self.prepare()
        self.assertFalse((self.store.root() / "evaluation.json").exists())
        with self.assertRaises(SliceError):
            self.store.confirm_evaluation(project["contract_sha256"], "a" * 40, project["evaluation_fingerprint"])
        self.assertEqual(self.confirm(project)["evaluation_status"], "confirmed")
        self.assertEqual(ProjectStore(self.root).inspect()["evaluation_status"], "confirmed")
        (self.repository / "train.py").write_text("NEW_HUMAN_WORK = True\n")
        status = self.store.inspect()
        self.assertFalse(status["workspace_prepared"])
        self.assertNotEqual(status["evaluation_status"], "confirmed")
        with self.assertRaises(SliceError):
            self.store.prepare(project["contract_sha256"])
        self.assertIn("NEW_HUMAN_WORK", (self.repository / "train.py").read_text())

    def test_protected_evaluation_paths_are_enforced_by_actual_diff(self):
        project = self.prepare()
        git = GitWorkspace(self.store.root(), self.repository)
        parent = git.prepare_parent(project["source_commit"], known_commits={project["source_commit"]})
        contract = load_contract(self.repository)
        for name in ("evaluate.py", "split.json", ".research_intern/contract.yaml", "azure_job.yaml", "unlisted.py"):
            path = self.repository / name
            original = path.read_bytes() if path.exists() else None
            path.write_bytes(b"# altered\n")
            with self.subTest(path=name), self.assertRaises(PermissionViolation):
                git.inspect_changes(parent, contract)
            path.unlink() if original is None else path.write_bytes(original)
        (self.repository / "train.py").write_text("NEW_CANDIDATE = True\n")
        _, changed = git.inspect_changes(parent, contract)
        self.assertEqual(changed, ("train.py",))

    def test_preflight_or_ignored_files_never_partially_initialize_source(self):
        for extra in ({"train.py": b"def broken(:\n"}, {".gitignore": b"cache.bin\n", "cache.bin": b"generated"},
                      {".env": b"EXAMPLE_PLACEHOLDER=no-real-secret\n"}, {".gitattributes": b"*.py filter=untrusted\n"}):
            # Each attempt uses its own fixed project slot under this test workspace.
            folder = self.root / str(len(list(self.root.iterdir())))
            folder.mkdir()
            store = ProjectStore(folder)
            files = {**sources(), **extra}
            uploaded = store.import_folder([SourceFile(f"Research/{n}", io.BytesIO(raw)) for n, raw in files.items()])
            with self.subTest(extra=extra), self.assertRaises(SliceError):
                store.prepare(uploaded["contract_sha256"])
            self.assertFalse((folder / PROJECT_PATH / ".git").exists())
            self.assertFalse((store.root() / "workspace.json").exists())
            for name, raw in files.items():
                self.assertEqual((folder / PROJECT_PATH / name).read_bytes(), raw)

    def test_existing_git_and_interrupted_setup_are_not_overwritten(self):
        uploaded = self.upload()
        git_dir = self.repository / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("Unregistered human history")
        with self.assertRaises(SliceError):
            self.store.prepare(uploaded["contract_sha256"])
        self.assertEqual((git_dir / "config").read_text(), "Unregistered human history")

    def test_preparation_ignores_inherited_git_redirects_hooks_and_config(self):
        uploaded = self.upload()
        hostile_config = self.root / "global-git-config"
        hostile_config.write_text("[core]\n hooksPath = /not/a/trusted/hook/path\n[include]\n path = /missing/config\n")
        with patch.dict("os.environ", {"GIT_CONFIG_GLOBAL": str(hostile_config),
                                       "GIT_DIR": str(self.root / "not-the-project"),
                                       "GIT_WORK_TREE": str(self.root),
                                       "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.bare",
                                       "GIT_CONFIG_VALUE_0": "true"}):
            project = self.store.prepare(uploaded["contract_sha256"])
        self.assertTrue(project["workspace_prepared"], project)
        self.assertFalse((self.root / "not-the-project").exists())

    def test_interruption_after_receipt_preserves_source_and_blocks_unsafe_retry(self):
        uploaded = self.upload()
        with patch.object(Path, "rename", side_effect=OSError("interrupted publication")):
            with self.assertRaises(OSError):
                self.store.prepare(uploaded["contract_sha256"])
        self.assertTrue((self.store.root() / "workspace.json").exists())
        self.assertEqual(self.store.inspect()["status"], "needs_attention")
        with self.assertRaises(SliceError):
            self.store.prepare(uploaded["contract_sha256"])
        for name, raw in sources().items():
            self.assertEqual((self.repository / name).read_bytes(), raw)


class EvaluationProtocolTests(WorkspaceTest):
    def contract(self):
        return json.loads(sources()[".research_intern/contract.yaml"])

    def test_backward_compatibility_and_normalized_protocol_fingerprint(self):
        plain = ExperimentContract.from_dict(json.loads(sources(evaluation=False)[".research_intern/contract.yaml"]))
        self.assertIsNone(plain.evaluation_fingerprint)
        self.assertNotIn("evaluation", plain.to_dict())
        contract = ExperimentContract.from_dict(self.contract())
        self.assertEqual(ExperimentContract.from_dict(contract.to_dict()), contract)
        for name in ("evaluate.py", "split.json"):
            self.assertFalse(contract.allows(name))
        changed = self.contract()
        changed["evaluation"]["procedure"] += " Changed policy."
        self.assertNotEqual(ExperimentContract.from_dict(changed).evaluation_fingerprint, contract.evaluation_fingerprint)
        changed = self.contract()
        changed["objective"]["direction"] = "minimize"
        self.assertNotEqual(ExperimentContract.from_dict(changed).evaluation_fingerprint, contract.evaluation_fingerprint)

    def test_incomplete_ambiguous_or_editable_evaluation_fails(self):
        variants = []
        for field in self.contract()["evaluation"]:
            changed = self.contract()
            del changed["evaluation"][field]
            variants.append(changed)
        for field, value in (("metric_definition", " "), ("procedure", 1), ("evaluator", "../eval.py"),
                             ("validation_split", "/outside.json"), ("evaluator_sha256", "not-a-digest")):
            changed = self.contract()
            changed["evaluation"][field] = value
            variants.append(changed)
        for field in ("evaluate.py", "split.json"):
            changed = self.contract()
            changed["scope"]["editable"].append(field)
            variants.append(changed)
        changed = self.contract()
        changed["constraints"]["latency_ms"] = {"min": 60, "max": 50}
        variants.append(changed)
        changed = self.contract()
        changed["scope"]["protected"] = ["experiment_outputs/"]
        variants.append(changed)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(SliceError):
                ExperimentContract.from_dict(value)

    def test_loader_rejects_altered_split_or_evaluator(self):
        repository = self.root / "source"
        repository.mkdir()
        for name, raw in sources().items():
            path = repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        load_contract(repository)
        for name in ("evaluate.py", "split.json"):
            path = repository / name
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            with self.subTest(name=name), self.assertRaisesRegex(SliceError, "SHA-256"):
                load_contract(repository)
            path.write_bytes(original)

    def test_outputs_must_identify_protocol_before_deterministic_evaluation(self):
        contract = ExperimentContract.from_dict(self.contract())
        request = JobRequest("EXP-000", None, "a" * 40)
        output = self.root / "outputs"
        write_outputs(output, contract.rules, request, score=0.5)
        run_path = output / "run.json"
        run = json.loads(run_path.read_text())
        for fingerprint in (None, "b" * 64):
            run["evaluation_fingerprint"] = fingerprint
            run_path.write_text(json.dumps(run))
            with self.subTest(fingerprint=fingerprint), self.assertRaisesRegex(OutputError, "evaluation_fingerprint"):
                collect_outputs(output, contract.rules, request, evaluation_fingerprint=contract.evaluation_fingerprint)
        run["evaluation_fingerprint"] = contract.evaluation_fingerprint
        run_path.write_text(json.dumps(run))
        result = collect_outputs(output, contract.rules, request, evaluation_fingerprint=contract.evaluation_fingerprint)
        self.assertEqual(evaluate_baseline(contract.rules, result).score, 0.5)
