"""Standard-library offline tests; all fixtures stay inside this workspace."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from research_intern.controller.execution import ExecutionController
from research_intern.domain.experiments import (
    Candidate, CollectedResult, Constraint, EvaluationRules, JobRequest, SliceError,
)
from research_intern.evaluation.evaluator import EvaluationError, evaluate, evaluate_baseline
from research_intern.execution.adapter import ExecutionError
from research_intern.execution.demo import prepare_git_fixture, run_demo
from research_intern.execution.outputs import OutputError, RunFailedError, collect_outputs
from research_intern.execution.simulated import SimulatedExecutor, write_outputs
from research_intern.ledger.sqlite import Ledger, LedgerError
from research_intern.main import main
from research_intern.workspace.paths import child_path

WORKSPACE = Path(__file__).resolve().parents[1]
BASE_COMMIT = "a" * 40
CANDIDATE_COMMIT = "b" * 40
RULES = EvaluationRules("validation_f1", "maximize", 0.87,
                        (Constraint("latency_ms", "max", 50),))


def candidate(parent: str = "EXP-000", commit: str = CANDIDATE_COMMIT) -> Candidate:
    return Candidate(parent, commit, "Weight decay may improve validation F1",
                     "Increase weight decay", "-WEIGHT_DECAY = 0.0\n+WEIGHT_DECAY = 0.01\n")


class WorkspaceTest(unittest.TestCase):
    def create_symlink(self, link: Path, target: Path, *, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege is unavailable; no elevation is requested")
            raise

    def setUp(self) -> None:
        parent = child_path(WORKSPACE, ".runtime", "test-workspaces")
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()


class EvaluationTests(unittest.TestCase):
    def score(self, value: float, *, latency: float = 40, parent: float = 0.8,
              best: float = 0.8, rules: EvaluationRules = RULES):
        return evaluate(rules, CollectedResult(value, {rules.metric: value, "latency_ms": latency}),
                        parent_score=parent, best_score=best)

    def test_improvement_regression_tie_and_goal(self) -> None:
        for value, decision, new_best in ((0.84, "KEEP", True), (0.75, "REJECT", False),
                                          (0.80, "REJECT", False), (0.87, "GOAL_REACHED", True)):
            with self.subTest(value=value):
                result = self.score(value)
                self.assertEqual(result.decision, decision)
                self.assertEqual(result.new_best, new_best)
                self.assertEqual(result.goal_reached, decision == "GOAL_REACHED")

    def test_minimization_and_minimum_constraints(self) -> None:
        rules = EvaluationRules("loss", "minimize", 0.2, (Constraint("latency_ms", "min", 30),))
        self.assertEqual(self.score(0.3, rules=rules).decision, "KEEP")
        self.assertEqual(self.score(0.9, rules=rules).decision, "REJECT")
        self.assertEqual(self.score(0.2, rules=rules).decision, "GOAL_REACHED")
        self.assertEqual(self.score(0.2, rules=rules, latency=29).decision, "REJECT")

    def test_constraint_violation_cannot_be_best_or_reach_goal(self) -> None:
        result = self.score(0.95, latency=51)
        self.assertTrue(result.improved_over_parent)
        self.assertFalse(result.constraints_satisfied)
        self.assertFalse(result.new_best)
        self.assertFalse(result.goal_reached)
        self.assertEqual(result.decision, "REJECT")
        self.assertEqual(result.constraint_results[0]["actual"], 51)
        self.assertTrue(self.score(0.84, latency=50).constraints_satisfied)

    def test_parent_improvement_does_not_necessarily_change_best(self) -> None:
        result = self.score(0.84, best=0.86)
        self.assertTrue(result.improved_over_parent)
        self.assertFalse(result.new_best)
        self.assertEqual(result.decision, "KEEP")

    def test_no_target_and_invalid_numeric_evidence(self) -> None:
        self.assertFalse(self.score(0.99, rules=replace(RULES, target=None)).goal_reached)
        for value in (True, float("nan"), float("inf"), "0.9", 10 ** 1000):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(EvaluationError):
                    self.score(value)
        with self.assertRaises(EvaluationError):
            evaluate(RULES, CollectedResult(0.9, {"validation_f1": 0.8, "latency_ms": 40}),
                     parent_score=0.8, best_score=0.8)

    def test_baseline_must_satisfy_constraints(self) -> None:
        result = CollectedResult(0.8, {"validation_f1": 0.8, "latency_ms": 60})
        with self.assertRaises(EvaluationError):
            evaluate_baseline(RULES, result)

    def test_rules_reject_invalid_direction_thresholds_and_conflicts(self) -> None:
        for options in ({"direction": "unknown"}, {"metric": 1}, {"metric": " "},
                        {"target": True}, {"target": float("nan")},
                        {"constraints": [Constraint("x", "min", 1)]},
                        {"constraints": (Constraint("x", "min", 2), Constraint("x", "max", 1))},
                        {"constraints": (Constraint("x", "min", 1), Constraint("x", "min", 2))}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                replace(RULES, **options)


class OutputTests(WorkspaceTest):
    def setUp(self) -> None:
        super().setUp()
        self.outputs = self.root / "experiment_outputs"
        self.request = JobRequest("EXP-001", "EXP-000", CANDIDATE_COMMIT)
        write_outputs(self.outputs, RULES, self.request)

    def change(self, name: str, edit) -> None:
        path = self.outputs / name
        content = json.loads(path.read_text(encoding="utf-8"))
        edit(content)
        path.write_text(json.dumps(content), encoding="utf-8")

    def collect(self):
        return collect_outputs(self.outputs, RULES, self.request)

    def test_valid_outputs_are_scored_from_metrics_not_logs(self) -> None:
        (self.outputs / "logs" / "simulation.log").write_text("Score is 999!", encoding="utf-8")
        self.assertEqual(self.collect().score, 0.84)

    def test_missing_output_is_not_inferred(self) -> None:
        (self.outputs / "metrics.json").unlink()
        with self.assertRaises(OutputError):
            self.collect()

    def test_output_versions_identity_and_metric_consistency(self) -> None:
        cases = (
            ("run.json", lambda d: d.update(schema_version="2.0")),
            ("run.json", lambda d: d.update(experiment_id="EXP-002")),
            ("run.json", lambda d: d.update(parent_experiment="EXP-999")),
            ("run.json", lambda d: d.pop("experiment_id")),
            ("run.json", lambda d: d.update(status="running")),
            ("run.json", lambda d: d.update(exit_code=1)),
            ("metrics.json", lambda d: d.update(schema_version="2.0")),
            ("metrics.json", lambda d: d["primary_metric"].update(name="accuracy")),
            ("metrics.json", lambda d: d["primary_metric"].update(direction="minimize")),
            ("metrics.json", lambda d: d["primary_metric"].update(value=0.1)),
            ("metrics.json", lambda d: d["metrics"].pop("latency_ms")),
            ("metrics.json", lambda d: d["primary_metric"].update(value=True)),
            ("metrics.json", lambda d: d["metrics"].update(latency_ms="40")),
            ("metrics.json", lambda d: d["metrics"].update(latency_ms=float("nan"))),
            ("metrics_history.json", lambda d: d.update(validation_f1=[True])),
        )
        for name, edit in cases:
            with self.subTest(name=name, edit=edit):
                path = self.outputs / name
                original = path.read_bytes()
                self.change(name, edit)
                with self.assertRaises(OutputError):
                    self.collect()
                path.write_bytes(original)

    def test_malformed_json_duplicate_keys_overflow_and_size_limit(self) -> None:
        path = self.outputs / "metrics.json"
        for content in (b"{", b"\xff", b"[]", b'{"x": 1, "x": 2}',
                        b'{"schema_version":"1.0", "primary_metric":{"name":"validation_f1",'
                        b'"direction":"maximize","value":1e999}, "metrics":{"validation_f1":1e999}}'):
            with self.subTest(content=content):
                path.write_bytes(content)
                with self.assertRaises(OutputError):
                    self.collect()
        with patch("research_intern.execution.outputs.MAX_JSON_BYTES", 1):
            with self.assertRaises(OutputError):
                self.collect()

    def test_workload_reported_failure_has_its_own_category(self) -> None:
        self.change("run.json", lambda d: d.update(status="failed"))
        (self.outputs / "metrics.json").unlink()
        with self.assertRaises(RunFailedError):
            self.collect()

    def test_required_directories_and_symlink_evidence(self) -> None:
        (self.outputs / "artifacts").rmdir()
        with self.assertRaises(OutputError):
            self.collect()
        (self.outputs / "artifacts").mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("Unrelated evidence", encoding="utf-8")
        try:
            (self.outputs / "logs" / "escape.txt").symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation is unavailable on this platform")
        with self.assertRaises(OutputError):
            self.collect()


class ControllerTests(WorkspaceTest):
    def setUp(self) -> None:
        super().setUp()
        self.ledger = Ledger(self.root, RULES, max_experiments=8)
        self.addCleanup(lambda: self.ledger.close())
        self.executor = SimulatedExecutor(self.root, RULES)
        self.controller = ExecutionController(self.ledger, self.executor)
        write_outputs(self.ledger.outputs("EXP-000"), RULES,
                      JobRequest("EXP-000", None, BASE_COMMIT), score=0.8)
        self.controller.import_baseline(BASE_COMMIT)

    def complete(self, scenario: str = "improve", value: Candidate | None = None):
        self.executor.scenario = scenario
        record = self.controller.start(value or candidate())
        for _ in range(3):
            if record.terminal:
                return record
            record = self.controller.advance(record.experiment_id)
        self.fail("The simulated job should have reached a terminal record")

    def test_job_id_is_durable_before_polling(self) -> None:
        record = self.controller.start(candidate())
        self.assertEqual(record.state, "SUBMITTED")
        poll = self.executor.get_status

        def check_saved(job_id):
            with contextlib.closing(sqlite3.connect(self.ledger.path)) as database:
                row = database.execute("SELECT job_id, git_commit FROM experiments WHERE experiment_id='EXP-001'").fetchone()
            self.assertEqual(row, (job_id, CANDIDATE_COMMIT))
            return poll(job_id)

        with patch.object(self.executor, "get_status", side_effect=check_saved):
            self.controller.advance(record.experiment_id)
        self.assertEqual(self.ledger.get("EXP-001").state, "RUNNING")

    def test_evaluation_and_all_failure_branches_remain_in_history(self) -> None:
        cases = (("improve", "KEEP", None), ("regress", "REJECT", None),
                 ("constraint", "REJECT", None),
                 ("runtime-failure", "FAILED", "EXECUTION_FAILED"),
                 ("invalid-output", "FAILED", "OUTPUT_INVALID"),
                 ("submission-failure", "FAILED", "SUBMISSION_FAILED"),
                 ("goal", "GOAL_REACHED", None))
        for index, (scenario, decision, failure) in enumerate(cases, 1):
            with self.subTest(scenario=scenario):
                record = self.complete(scenario)
                self.assertEqual(record.experiment_id, f"EXP-{index:03d}")
                self.assertEqual(record.decision, decision)
                self.assertEqual(record.failure_type, failure)
                self.assertEqual(record.parent_commit, BASE_COMMIT)
                if scenario != "submission-failure":
                    self.assertTrue(record.outputs_path)
                    self.assertTrue((self.root / record.outputs_path / "logs" / "simulation.log").is_file())
        self.assertEqual(len(self.ledger.history()), 8)
        self.assertEqual(self.ledger.best().experiment_id, "EXP-007")
        self.assertEqual(self.ledger.history()[-1].experiment_id, "EXP-007")

    def test_reopen_and_resume_reuses_existing_job(self) -> None:
        record = self.controller.start(candidate())
        self.controller.advance(record.experiment_id)
        self.ledger.close()
        self.ledger = Ledger(self.root, RULES)
        executor = SimulatedExecutor(self.root, RULES)
        controller = ExecutionController(self.ledger, executor)
        with patch.object(executor, "submit_job", side_effect=AssertionError("Duplicate submission")):
            finished = controller.advance(record.experiment_id)
            again = controller.advance(record.experiment_id)
        self.assertEqual(finished, again)
        self.assertEqual(finished.decision, "KEEP")
        self.assertEqual(len(self.ledger.history()), 2)

    def test_serial_execution_and_invalid_parent_are_blocked(self) -> None:
        self.controller.start(candidate())
        with self.assertRaises(LedgerError):
            self.controller.start(candidate())
        self.controller.advance("EXP-001")
        self.controller.advance("EXP-001")
        self.complete("regress")
        with self.assertRaises(LedgerError):
            self.controller.start(candidate("EXP-002", "c" * 40))

    def test_ambiguous_submission_never_retries_automatically(self) -> None:
        submit = self.executor.submit_job

        def interrupted(request):
            submit(request)
            raise TimeoutError("Response lost after the job was created")

        with patch.object(self.executor, "submit_job", side_effect=interrupted):
            with self.assertRaises(TimeoutError):
                self.controller.start(candidate())
        self.assertEqual(self.ledger.get("EXP-001").state, "SUBMITTING")
        with self.assertRaisesRegex(SliceError, "reconcile"):
            self.controller.resume_submission("EXP-001")
        with self.assertRaises(LedgerError):
            self.controller.start(candidate())
        # Explicit reconciliation attaches the already-created job, without submission.
        self.ledger.record_job("EXP-001", "simulated-EXP-001")
        self.controller.advance("EXP-001")
        self.assertEqual(self.controller.advance("EXP-001").decision, "KEEP")

    def test_prepared_candidate_can_resume_without_a_new_id(self) -> None:
        self.ledger.reserve(candidate())
        record = self.controller.resume_submission("EXP-001")
        self.assertEqual(record.state, "SUBMITTED")
        self.assertEqual(len(self.ledger.history()), 2)

    def test_transient_status_and_download_errors_are_resumable(self) -> None:
        self.controller.start(candidate())
        with patch.object(self.executor, "get_status", side_effect=ExecutionError("Temporary status error")):
            with self.assertRaises(ExecutionError):
                self.controller.advance("EXP-001")
        self.assertEqual(self.ledger.get("EXP-001").state, "SUBMITTED")
        self.controller.advance("EXP-001")
        with patch.object(self.executor, "download_outputs", side_effect=ExecutionError("Temporary download error")):
            with self.assertRaises(ExecutionError):
                self.controller.advance("EXP-001")
        self.assertEqual(self.ledger.get("EXP-001").job_status, "completed")
        self.assertEqual(self.controller.advance("EXP-001").decision, "KEEP")

    def test_published_outputs_are_reused_after_recording_interruption(self) -> None:
        self.controller.start(candidate())
        self.controller.advance("EXP-001")
        with patch.object(self.ledger, "record_result", side_effect=OSError("Interrupted write")):
            with self.assertRaises(OSError):
                self.controller.advance("EXP-001")
        with patch.object(self.executor, "download_outputs", side_effect=AssertionError("Do not overwrite evidence")):
            self.assertEqual(self.controller.advance("EXP-001").decision, "KEEP")

    def test_evaluator_failure_is_not_a_quality_regression(self) -> None:
        self.controller.start(candidate())
        self.controller.advance("EXP-001")
        with patch("research_intern.controller.execution.evaluate", side_effect=EvaluationError("Inconsistent evidence")):
            result = self.controller.advance("EXP-001")
        self.assertEqual(result.failure_type, "EVALUATION_INVALID")
        self.assertIsNone(result.evaluation)
        self.assertEqual(self.ledger.best().experiment_id, "EXP-000")

    def test_cancelled_job_is_preserved_as_execution_failure(self) -> None:
        record = self.controller.start(candidate())
        self.executor.cancel_job(record.job_id)
        result = self.controller.advance(record.experiment_id)
        self.assertEqual(result.failure_type, "EXECUTION_FAILED")
        self.assertEqual(result.job_status, "cancelled")

    def test_immutable_rules_baseline_and_terminal_records(self) -> None:
        with self.assertRaises(LedgerError):
            Ledger(self.root, replace(RULES, target=0.5))
        with self.assertRaises(LedgerError):
            self.controller.import_baseline(BASE_COMMIT)
        self.complete()
        with self.assertRaises(LedgerError):
            self.ledger.record_failure("EXP-001", "OUTPUT_INVALID", "Cannot rewrite a result")

    def test_invalid_commit_and_live_adapter_are_rejected(self) -> None:
        for commit in ("HEAD", "short", BASE_COMMIT):
            with self.subTest(commit=commit), self.assertRaises(LedgerError):
                self.controller.start(candidate(commit=commit))
        with patch.object(self.executor, "backend", "azure_ml"):
            with self.assertRaises(SliceError):
                ExecutionController(self.ledger, self.executor)

    def test_duplicate_job_id_and_traversal_are_rejected(self) -> None:
        self.complete()
        second = self.ledger.reserve(candidate())
        self.ledger.begin_submission(second.experiment_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.record_job(second.experiment_id, "simulated-EXP-001")
        with self.assertRaises(LedgerError):
            self.ledger.outputs("../../outside")
        with self.assertRaises(SliceError):
            child_path(self.root, "..", "outside")

    def test_candidate_cannot_start_without_a_baseline(self) -> None:
        empty = self.root / "empty_run"
        empty.mkdir()
        with Ledger(empty, RULES) as ledger:
            controller = ExecutionController(ledger, self.executor)
            with patch.object(self.executor, "submit_job") as submit:
                with self.assertRaises(LedgerError):
                    controller.start(candidate())
                submit.assert_not_called()

    def test_completed_job_with_failed_run_is_recorded_as_execution_failure(self) -> None:
        self.controller.start(candidate())
        self.controller.advance("EXP-001")
        download = self.executor.download_outputs

        def workload_failure(job_id, destination):
            download(job_id, destination)
            run = destination / "run.json"
            payload = json.loads(run.read_text(encoding="utf-8"))
            payload["status"] = "failed"
            run.write_text(json.dumps(payload), encoding="utf-8")

        with patch.object(self.executor, "download_outputs", side_effect=workload_failure):
            record = self.controller.advance("EXP-001")
        self.assertEqual(record.failure_type, "EXECUTION_FAILED")
        self.assertEqual(record.job_status, "completed")
        self.assertIsNone(record.evaluation)


class DemoTests(WorkspaceTest):
    def test_demo_has_real_git_anchors_and_synthetic_evidence(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            root, result, best = run_demo(self.root)
        self.assertTrue(root.is_relative_to(self.root))
        self.assertEqual(result.decision, "KEEP")
        self.assertEqual(best, "EXP-001")
        self.assertTrue((root / "fixture_repo" / ".git").is_dir())
        self.assertNotEqual(result.git_commit, result.parent_commit)
        self.assertIn("+WEIGHT_DECAY = 0.01", result.diff)
        self.assertTrue(json.loads((root / result.outputs_path / "run.json").read_text())["simulation"])
        with contextlib.closing(sqlite3.connect(root / "ledger.sqlite3")) as database:
            events = database.execute("SELECT state, job_id FROM events WHERE experiment_id='EXP-001' ORDER BY sequence").fetchall()
        self.assertEqual([row[0] for row in events],
                         ["PREPARED", "SUBMITTING", "SUBMITTED", "RUNNING", "RUNNING", "RECORDED"])
        self.assertEqual(events[2][1], result.job_id)

    def test_git_fixture_cannot_reuse_an_existing_directory(self) -> None:
        (self.root / "fixture_repo").mkdir()
        with self.assertRaises(FileExistsError):
            prepare_git_fixture(self.root)

    def test_git_fixture_ignores_ambient_repository_redirects(self) -> None:
        unrelated = self.root / "unrelated_repository"
        unrelated.mkdir()
        with patch.dict(os.environ, {"GIT_DIR": str(unrelated), "GIT_WORK_TREE": str(unrelated),
                                     "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                                     "GIT_CONFIG_VALUE_0": str(unrelated)}):
            baseline, commit, diff = prepare_git_fixture(self.root)
        self.assertNotEqual(baseline, commit)
        self.assertIn("WEIGHT_DECAY", diff)
        self.assertEqual(list(unrelated.iterdir()), [])

    def test_simulation_entry_point_does_not_import_copilot(self) -> None:
        with patch("research_intern.execution.demo.run_demo", return_value=(self.root, type("Record", (), {
            "experiment_id": "EXP-001", "decision": "KEEP", "failure_type": None,
        })(), "EXP-001")) as run:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["simulate"]), 0)
        run.assert_called_once_with(WORKSPACE, "improve")
        self.assertIn("Copilot usage: 0", output.getvalue())

    def test_existing_live_opt_in_guard_still_applies_without_sdk(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["spike", "--working-directory", "fixture", "--runtime-path", "runtime"])
        self.assertEqual(raised.exception.code, 2)

    def test_simulation_rejects_a_symlink_outside_its_workspace(self) -> None:
        application = self.root / "application"
        application.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (application / ".runtime").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation is unavailable on this platform")
        with self.assertRaises(SliceError):
            run_demo(application)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
