"""Automatic onboarding uses the real scorer protocol, without any cloud calls."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

import yaml

from research_intern.domain.experiments import SliceError
from research_intern.evaluation.runtime import prepare_runtime
from research_intern.evaluation.trusted import TrustedScorer
from research_intern.workspace.autoscoring import discover
from research_intern.workspace.score_runner import artifact_file
from research_intern.workspace.setup import build_contract, prepare_discovered
from research_intern.execution.discovery import AzureDiscovery
from test_execution_slice import WorkspaceTest
import test_onboarding as fixtures


class AutomaticEvaluationTests(fixtures.OnboardingFixture):
    def automatic(self):
        self.choices.update(evaluation_file="", validation_file="", evaluation_command="", scoring_python="")
        self.onboarding.save(self.choices)

    def test_discovers_inputs_without_importing_user_source(self):
        evaluator = self.onboarding.root / "repository/evaluate.py"
        evaluator.write_text("raise RuntimeError('must never execute during inspection')\n" + evaluator.read_text())
        self.automatic()
        state = self.onboarding.snapshot()
        self.assertEqual(state["evaluation_file"], "evaluate.py")
        self.assertEqual(state["validation_file"], "split.json")
        self.assertEqual(state["evaluation_setup"]["status"], "ready")
        self.assertIn("{predictions}", state["evaluation_setup"]["command"])
        self.assertFalse((self.onboarding.root / "scoring-environments").exists())

    def test_blank_command_reuses_baseline_and_scores_candidate_independently(self):
        self.automatic()
        live = self.prepare(existing=True)
        live.initialize()
        gateway = fixtures.NativeGateway()
        asyncio.run(live.run(gateway=gateway, proposer_factory=lambda: fixtures.Editor([]), emit=lambda _: None))
        self.assertEqual(gateway.submissions, [])
        self.assertTrue(live.inspect()["baseline_accepted"])
        receipt = json.loads((self.onboarding.root / "experiments/EXP-000/verified-score.json").read_text())
        self.assertEqual(receipt["metrics"]["f1"], .5)  # Training falsely reported .999.
        live.activate()
        asyncio.run(live.run(gateway=gateway, proposer_factory=lambda: fixtures.Editor([]), emit=lambda _: None))
        self.assertEqual(len(gateway.submissions), 1)
        receipt = json.loads((self.onboarding.root / "experiments/EXP-001/verified-score.json").read_text())
        self.assertEqual(receipt["metrics"]["f1"], .8)

    def nested_project(self):
        root = self.onboarding.root
        source = root / "repository"
        (source / "evaluate.py").unlink()
        files = {
            "src/model/__init__.py": "",
            "src/model/evaluation/__init__.py": "",
            "src/model/evaluation/score.py": """import argparse,json
from pathlib import Path
from model.data.dataset import scale
p=argparse.ArgumentParser()
p.add_argument('--data-root',required=True)
p.add_argument('--manifest',required=True)
p.add_argument('--predictions',required=True)
p.add_argument('--output',required=True)
a=p.parse_args()
values=json.loads(Path(a.predictions).read_text())
Path(a.output).write_text(json.dumps({'f1':sum(values)/len(values)/scale(Path(a.data_root), Path(a.manifest))}))
""",
            "src/model/data/__init__.py": "",
            "src/model/data/dataset.py": """import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
def scale(data, manifest):
    policy = json.loads((ROOT / 'configs' / 'policy.json').read_text())
    assert json.loads(manifest.read_text())['scale'] == 1
    return float((data / 'reference.bin').read_text()) * policy['factor']
""",
            "configs/policy.json": '{"factor": 1}',
        }
        metadata = json.loads((root / "project.json").read_text())
        for name, text in files.items():
            # Reproduce importer retention of src/model/data/*.py as assets.
            target = (root / "assets" if "/data/" in name else source) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            if "/data/" in name:
                metadata["assets"][name] = {"bytes": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        data = root / "assets/dataset/reference.bin"
        data.parent.mkdir(exist_ok=True)
        data.write_bytes(b"1")
        metadata["assets"]["dataset/reference.bin"] = {"bytes": 1, "sha256": hashlib.sha256(b"1").hexdigest()}
        (root / "project.json").write_text(json.dumps(metadata))
        job = yaml.safe_load((source / "job.yaml").read_text())
        job["inputs"]["dataset"] = {"path": "./dataset", "type": "uri_folder"}
        (source / "job.yaml").write_text(yaml.safe_dump(job))
        self.onboarding.scan()
        self.choices.update(evaluation_file="", validation_file="", evaluation_command="", scoring_python="",
                            editable=["train.py", "configs/policy.json"])
        self.onboarding.save(self.choices)

    def test_src_layout_retained_data_and_frozen_helpers_score_successfully(self):
        self.nested_project()
        live = self.prepare(existing=True)
        config = live.configuration()
        self.assertIn("src/model/data/dataset.py", config["scorer_files"])
        self.assertIn("configs/policy.json", config["scorer_files"])
        live.initialize()
        asyncio.run(live.run(gateway=fixtures.NativeGateway(), proposer_factory=lambda: fixtures.Editor([]), emit=lambda _: None))
        self.assertTrue(live.inspect()["baseline_accepted"])
        data = self.onboarding.root / "assets/dataset/reference.bin"
        data.write_text("2")
        from research_intern.contracts.loader import load_contract
        with self.assertRaisesRegex(SliceError, "Retained evaluation input changed"):
            TrustedScorer(self.onboarding.root, load_contract(self.onboarding.root / "repository"),
                          python=sys.executable, files=config["scorer_files"], timeout_seconds=30)

    def test_unknown_required_argument_names_the_missing_input(self):
        evaluator = self.onboarding.root / "repository/evaluate.py"
        evaluator.write_text(evaluator.read_text().replace("a=p.parse_args()", "p.add_argument('--labels', required=True)\na=p.parse_args()"))
        self.automatic()
        state = self.onboarding.snapshot()
        self.assertEqual(state["evaluation_setup"]["status"], "needs_input")
        self.assertIn("--labels", state["evaluation_setup"]["message"])
        with self.assertRaisesRegex(SliceError, "--labels"):
            self.prepare()
        self.assertFalse((self.onboarding.root / "live.json").exists())

    def test_ambiguous_evaluators_are_not_arbitrarily_selected(self):
        source = self.onboarding.root / "repository"
        (source / "score.py").write_bytes((source / "evaluate.py").read_bytes())
        self.automatic()
        plan = self.onboarding.snapshot()["evaluation_setup"]
        self.assertEqual(plan["status"], "needs_input")
        self.assertIn("More than one scoring script", plan["message"])

    def test_logs_only_run_fails_before_runtime_download_or_publication(self):
        self.automatic()
        self.select_existing()
        self.cloud.status = "Completed"
        self.onboarding.poll_once()
        for file in (self.onboarding.root / "imported-jobs").rglob("predictions.json"):
            file.unlink()
        self.onboarding.save({**self.choices, "existing_compatible": True})
        with patch("research_intern.workspace.setup.prepare_runtime") as runtime:
            with self.assertRaisesRegex(SliceError, "Saved predictions/checkpoint are missing"):
                prepare_discovered(self.onboarding, authorized=True)
            runtime.assert_not_called()
        self.assertFalse((self.onboarding.root / "live.json").exists())

    def test_no_scores_from_metrics_json_or_ambiguous_predictions(self):
        outputs = self.root / "saved"
        outputs.mkdir()
        (outputs / "metrics.json").write_text('{"f1":1}')
        with self.assertRaisesRegex(RuntimeError, "missing"):
            artifact_file(outputs, ["predictions.json"])
        for folder in ("train", "validation"):
            (outputs / folder).mkdir()
            (outputs / folder / "predictions.json").write_text('[1]')
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            artifact_file(outputs, ["predictions.json"])

    def test_setup_failure_keeps_project_unprepared(self):
        self.automatic()
        self.onboarding.find_jobs(); self.onboarding.new_baseline()
        with patch("research_intern.workspace.setup.prepare_runtime", side_effect=SliceError("network blocked")):
            with self.assertRaisesRegex(SliceError, "network blocked"):
                prepare_discovered(self.onboarding, authorized=True)
        self.assertFalse((self.onboarding.root / "workspace.json").exists())
        self.assertFalse((self.onboarding.root / "live.json").exists())

    def test_legacy_selection_of_git_control_file_is_normalized(self):
        self.automatic()
        self.choices["editable"] = ["train.py", ".gitignore"]
        self.onboarding.save(self.choices)
        live = self.prepare()
        self.assertIn(".gitignore", live.configuration()["scorer_files"])
        self.assertNotIn(".gitignore", self.onboarding.snapshot()["editable"])

    def test_sibling_and_namespace_helpers_are_local_frozen_imports(self):
        from research_intern.workspace.autoscoring import dependencies
        source = self.onboarding.root / "repository"
        folder = source / "src/scoring"
        folder.mkdir(parents=True)
        (folder / "score.py").write_text("from helper import score\nfrom scoring.metrics import value\n")
        (folder / "helper.py").write_text("def score(): return 1\n")
        (folder / "metrics.py").write_text("value = 1\n")
        files, external, roots = dependencies(source, self.onboarding.root / "assets", "src/scoring/score.py")
        self.assertIn("src/scoring/helper.py", files)
        self.assertIn("src/scoring/metrics.py", files)
        self.assertEqual(external, [])
        self.assertIn("src/scoring", roots)

    def test_legacy_dashboard_detects_once_until_explicit_rescan(self):
        self.automatic()
        state = self.onboarding._read()
        state.pop("evaluation_setup")
        self.onboarding._write(state)
        with patch.object(self.onboarding, "_discover_evaluation", wraps=self.onboarding._discover_evaluation) as detect:
            self.onboarding.snapshot(); self.onboarding.snapshot()
            self.assertEqual(detect.call_count, 1)
            self.onboarding.scan()
            self.assertEqual(detect.call_count, 2)


class RuntimeTests(WorkspaceTest):
    def test_missing_named_outputs_are_requested_and_existing_outputs_are_preserved(self):
        output = self.root / "named-outputs/results"
        output.mkdir(parents=True)
        (output / "predictions.json").write_text("[]")
        client = SimpleNamespace(jobs=Mock())
        client.jobs.get.return_value = SimpleNamespace(outputs={"results": object(), "more_results": object()})
        cloud = AzureDiscovery(lambda: None)
        with patch.object(cloud, "client", return_value=nullcontext(client)):
            cloud.ensure_outputs(fixtures.TARGET, "job-one", self.root)
        client.jobs.download.assert_called_once_with(name="job-one", download_path=str(self.root), output_name="more_results")

    def test_stdlib_scoring_needs_no_install(self):
        spec = {"requires_python": ">=3.11", "requirements": [], "imports": []}
        with patch("research_intern.evaluation.runtime.run_command") as install:
            self.assertEqual(prepare_runtime(self.root, spec), sys.executable)
        install.assert_not_called()

    def test_provisions_cpu_dependencies_in_owned_venv_and_reuses_it(self):
        spec = {"requires_python": ">=3.10,<3.14", "requirements": ["torch==2.9.1", "numpy>=2"], "imports": ["torch", "numpy"]}
        commands = []
        import os
        suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
        def install(command, log, environment, cwd, **kwargs):
            command = list(map(str, command)); commands.append(command)
            self.assertEqual(environment["UV_NO_CONFIG"], "1")
            self.assertTrue(Path(cwd).is_relative_to(self.root))
            if "venv" in command:
                target = Path(command[-1]) / suffix
                target.parent.mkdir(parents=True, exist_ok=True); target.touch()
        with patch("research_intern.evaluation.runtime.install_uv", return_value=self.root / "standalone-uv"), patch("research_intern.evaluation.runtime.run_command", side_effect=install), patch("research_intern.evaluation.runtime.probe", return_value={"python": "3.12.9", "versions": {}}):
            python = prepare_runtime(self.root, spec)
            first = len(commands)
            self.assertEqual(prepare_runtime(self.root, spec), python)
            self.assertEqual(len(commands), first)
        self.assertTrue(Path(python).is_relative_to(self.root / "scoring-environments"))
        self.assertTrue(any("https://download.pytorch.org/whl/cpu" in c for c in commands))
        self.assertFalse(any(sys.executable in c and "install" in c for c in commands))
        self.assertTrue(all(c[0] == str(self.root / "standalone-uv") for c in commands))
