"""Offline repetition and interruption tests using real local Git and SQLite."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
from dataclasses import asdict
from unittest.mock import patch

from research_intern.domain.experiments import JobRequest
from research_intern.execution.adapter import ExecutionError
from research_intern.ledger.sqlite import Ledger, LedgerError
from research_intern.main import main
from research_intern.offline import compose_loop, initialize_run, inspect_run
from research_intern.validation.preflight import PreflightError
from research_intern.workspace.git import WorkspaceError
from research_intern.workspace.lock import RunLock
from research_intern.workspace.recovery import archive_failed_edits

from test_candidate_slice import MutationProposer
from test_execution_slice import WORKSPACE, WorkspaceTest, candidate


class LoopFixture(WorkspaceTest):
    def setup_loop(self, **options):
        self.run_root = initialize_run(self.root, **options)
        self.reopen()

    def reopen(self):
        if hasattr(self, "ledger"):
            self.ledger.close()
        self.ledger = Ledger.reopen(self.run_root)
        self.addCleanup(self.ledger.close)
        self.loop = compose_loop(self.ledger)
        self.git = self.loop.workspace
        self.repository = self.git.repository
        self.journal = self.loop.journal

    def drive(self, **options):
        return asyncio.run(self.loop.run(poll_interval=0, **options))

    def prepare(self):
        return asyncio.run(self.loop.candidates.prepare_candidate())

    def latest_attempt(self):
        return self.run_root / "candidate_attempts" / self.journal.latest().attempt_id

    def request(self, record):
        return JobRequest(record.experiment_id, record.parent_experiment, record.git_commit)

    def no_jobs(self):
        self.assertEqual(list(self.loop.execution.executor.jobs.glob("*.json")), [])


class RepetitionTests(LoopFixture):
    def test_mixed_loop_keeps_lineage_and_negative_evidence_until_goal(self):
        self.setup_loop()
        status = self.drive()
        records = self.ledger.history()
        self.assertEqual([r.decision for r in records], ["KEEP", "KEEP", "REJECT", "FAILED", "GOAL_REACHED"])
        self.assertEqual([r.parent_experiment for r in records[1:]], ["EXP-000", "EXP-001", "EXP-001", "EXP-001"])
        self.assertEqual(len({r.git_commit for r in records}), 5)
        self.assertEqual(status["preparation_budget"]["used"], 4)
        self.assertIn("GOAL_REACHED", status["controller"]["message"])
        state = status["research_state"]
        self.assertEqual(state["best_experiment"]["experiment_id"], "EXP-004")
        self.assertEqual(state["rejected_directions"][0]["experiment_id"], "EXP-002")
        self.assertEqual(state["known_failures"][0]["experiment_id"], "EXP-003")
        context = json.loads((self.latest_attempt() / "context.json").read_text())
        self.assertEqual(context["research_state"]["last_experiment"]["experiment_id"], "EXP-003")
        self.git.verify_clean(records[-1].git_commit)
        self.reopen()
        with patch.object(self.loop.candidates, "prepare_candidate", side_effect=AssertionError("extra proposal")):
            self.drive()
        self.assertEqual(len(self.ledger.history()), 5)

    def test_experiment_cap_restores_best_after_regression(self):
        self.setup_loop(max_experiments=2)
        status = self.drive()
        self.assertIn("EXPERIMENT_BUDGET_EXHAUSTED", status["controller"]["message"])
        self.assertEqual(self.ledger.history()[-1].decision, "REJECT")
        self.git.verify_clean(self.ledger.get("EXP-001").git_commit)
        self.assertEqual(len(list(self.loop.execution.executor.jobs.glob("*.json"))), 2)

    def test_zero_preparation_budget_never_starts_editor(self):
        self.setup_loop(max_attempts=0)
        with patch.object(self.loop.candidates, "proposer_factory", side_effect=AssertionError("editor started")):
            status = self.drive()
        self.assertIn("PREPARATION_BUDGET_EXHAUSTED", status["controller"]["message"])
        self.assertEqual(len(self.ledger.history()), 1)
        self.no_jobs()

    def test_invalid_candidate_retry_has_own_budget_and_failure_handoff(self):
        self.setup_loop(max_experiments=1, max_attempts=2, scenario="invalid-first")
        status = self.drive()
        self.assertEqual(status["preparation_budget"]["used"], 2)
        self.assertEqual([r.experiment_id for r in self.ledger.history()], ["EXP-000", "EXP-001"])
        context = json.loads((self.latest_attempt() / "context.json").read_text())
        self.assertEqual(context["attempt_number"], 2)
        failures = context["preparation_failures"]
        self.assertEqual(failures[0]["category"], "CANDIDATE_INVALID")
        failed = self.run_root / "candidate_attempts" / failures[0]["attempt_id"]
        self.assertTrue((failed / "rejected_files" / "labeller.py").is_file())

    def test_invalid_attempt_limit_survives_restart_without_reserving_ids(self):
        self.setup_loop(max_attempts=2, scenario="invalid-always")
        self.drive(steps=1)
        self.reopen()
        status = self.drive()
        self.assertEqual(status["preparation_budget"], {"limit": 2, "used": 2, "remaining": 0})
        self.assertIn("PREPARATION_BUDGET_EXHAUSTED", status["controller"]["message"])
        self.assertEqual(len(self.ledger.history()), 1)
        self.no_jobs()
        self.git.verify_clean(self.ledger.best().git_commit)

    def test_protected_edit_is_archived_and_restored_before_retry(self):
        self.setup_loop(max_experiments=1, scenario="protected-first")
        original = (self.repository / "evaluate.py").read_bytes()
        self.drive()
        self.assertEqual((self.repository / "evaluate.py").read_bytes(), original)
        failure = self.journal.failures()[0]
        self.assertEqual(failure["category"], "PERMISSION_VIOLATION")
        archived = self.run_root / "candidate_attempts" / failure["attempt_id"] / "rejected_files" / "evaluate.py"
        self.assertNotEqual(archived.read_bytes(), original)
        self.assertEqual(self.ledger.get("EXP-001").decision, "KEEP")

    def test_loop_policy_cannot_change_or_be_bypassed_by_direct_reservation(self):
        self.setup_loop(max_experiments=1, max_attempts=0)
        with self.assertRaises(LedgerError):
            self.journal.configure(max_attempts=10, scenario="mixed")
        with self.assertRaisesRegex(LedgerError, "journaled preparation"):
            self.ledger.reserve(candidate())
        self.assertEqual(len(self.ledger.history()), 1)


class JobRecoveryTests(LoopFixture):
    def test_prepared_resume_submits_final_reserved_slot_exactly_once(self):
        self.setup_loop(max_experiments=1)
        original = self.prepare()
        self.reopen()
        submit = self.loop.execution.executor.submit_job
        with patch.object(self.loop.execution.executor, "submit_job", wraps=submit) as calls:
            self.drive()
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(self.ledger.get("EXP-001").git_commit, original.git_commit)
        self.assertEqual(self.journal.count(), 1)

    def test_submitted_resume_reuses_durable_job(self):
        self.setup_loop(max_experiments=1)
        self.drive(steps=2)
        job_id = self.ledger.get("EXP-001").job_id
        self.reopen()
        with patch.object(self.loop.execution.executor, "submit_job", side_effect=AssertionError("duplicate job")):
            self.drive()
        self.assertEqual(self.ledger.get("EXP-001").job_id, job_id)
        self.assertEqual(self.ledger.get("EXP-001").decision, "KEEP")

    def test_interrupted_submission_attaches_matching_job_without_resubmission(self):
        self.setup_loop(max_experiments=1)
        record = self.prepare()
        self.ledger.begin_submission(record.experiment_id)
        job_id = self.loop.execution.executor.submit_job(self.request(record))
        self.reopen()
        with patch.object(self.loop.execution.executor, "submit_job", side_effect=AssertionError("duplicate job")):
            self.drive()
        self.assertEqual(self.ledger.get(record.experiment_id).job_id, job_id)
        self.assertEqual(self.ledger.get(record.experiment_id).decision, "KEEP")

    def test_interrupted_submission_without_job_records_failure_and_never_resubmits(self):
        self.setup_loop(max_experiments=1)
        record = self.prepare()
        self.ledger.begin_submission(record.experiment_id)
        self.reopen()
        with patch.object(self.loop.execution.executor, "submit_job", side_effect=AssertionError("resubmission")):
            self.drive()
        self.assertEqual(self.ledger.get(record.experiment_id).failure_type, "SUBMISSION_FAILED")
        self.no_jobs()

    def test_mismatched_interrupted_job_requires_reconciliation(self):
        self.setup_loop(max_experiments=1)
        record = self.prepare()
        self.ledger.begin_submission(record.experiment_id)
        wrong = JobRequest(record.experiment_id, record.parent_experiment, "a" * 40)
        self.loop.execution.executor.submit_job(wrong)
        with self.assertRaisesRegex(ExecutionError, "does not match"):
            self.drive()
        self.assertEqual(self.ledger.get(record.experiment_id).state, "SUBMITTING")
        self.assertEqual(self.journal.loop_status()["state"], "RECOVERY_REQUIRED")

    def test_completed_job_retries_download_after_restart_without_extra_poll_or_submit(self):
        self.setup_loop(max_experiments=1)
        self.drive(steps=3)
        with patch.object(self.loop.execution.executor, "download_outputs", side_effect=ExecutionError("temporary failure")):
            with self.assertRaises(ExecutionError):
                self.drive()
        self.assertEqual(self.ledger.get("EXP-001").job_status, "completed")
        self.reopen()
        with patch.object(self.loop.execution.executor, "get_status", side_effect=AssertionError("extra poll")), \
                patch.object(self.loop.execution.executor, "submit_job", side_effect=AssertionError("extra job")):
            self.drive()
        self.assertEqual(self.ledger.get("EXP-001").decision, "KEEP")


class PreparationRecoveryTests(LoopFixture):
    def test_interruption_before_commit_recovers_without_calling_editor_again(self):
        self.setup_loop(max_experiments=1)
        with patch.object(self.git, "commit_candidate", side_effect=OSError("interrupted before Git")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.journal.latest().stage, "COMMITTING")
        self.reopen()
        with patch.object(self.loop.candidates, "proposer_factory", side_effect=AssertionError("second editor")):
            self.drive()
        self.assertEqual(self.journal.count(), 1)
        self.assertEqual(self.ledger.get("EXP-001").decision, "KEEP")

    def test_interruption_after_staging_finishes_the_validated_commit(self):
        self.setup_loop(max_experiments=1)
        git = self.git._git

        def stop_commit(*args):
            if args[0] == "commit":
                raise OSError("interrupted after staging")
            return git(*args)

        with patch.object(self.git, "_git", side_effect=stop_commit):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.journal.latest().stage, "COMMITTING")
        self.reopen()
        self.drive()
        self.assertEqual(self.journal.count(), 1)
        self.assertEqual(self.ledger.get("EXP-001").decision, "KEEP")

    def test_interruption_after_git_commit_adopts_exact_commit_without_new_attempt(self):
        self.setup_loop(max_experiments=1)
        commit = self.git.commit_candidate

        def stop_after_commit(*args):
            commit(*args)
            raise OSError("interrupted before journal checkpoint")

        with patch.object(self.git, "commit_candidate", side_effect=stop_after_commit):
            with self.assertRaises(OSError):
                self.prepare()
        original = self.git.head
        self.assertNotEqual(original, self.ledger.best().git_commit)
        self.reopen()
        self.drive()
        self.assertEqual(self.ledger.get("EXP-001").git_commit, original)
        self.assertEqual(self.journal.count(), 1)

    def test_committed_checkpoint_reserves_once_after_restart(self):
        self.setup_loop(max_experiments=1)
        with patch.object(self.loop.candidates, "reserve_preparation", side_effect=OSError("interrupted before reservation")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.journal.latest().stage, "COMMITTED")
        original = self.git.head
        self.reopen()
        self.drive()
        self.assertEqual(self.ledger.get("EXP-001").git_commit, original)
        self.assertEqual(len(self.ledger.history()), 2)

    def test_reservation_transaction_recovers_missing_evidence_without_new_id(self):
        self.setup_loop(max_experiments=1)
        reserve = self.ledger.reserve

        def stop_after_reserve(*args, **kwargs):
            reserve(*args, **kwargs)
            raise OSError("interrupted after transaction")

        with patch.object(self.ledger, "reserve", side_effect=stop_after_reserve):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.journal.latest().stage, "RESERVED")
        self.assertEqual(self.journal.latest().experiment_id, "EXP-001")
        self.reopen()
        with patch.object(self.ledger, "reserve", side_effect=AssertionError("second reservation")):
            self.drive()
        self.assertTrue((self.ledger.directory("EXP-001") / "candidate.json").is_file())
        self.assertTrue((self.latest_attempt() / "result.json").is_file())
        self.assertEqual(len(self.ledger.history()), 2)

    def test_newer_human_edits_are_preserved_and_block_cleanup(self):
        self.setup_loop(scenario="invalid-always")
        with self.assertRaises(PreflightError):
            self.prepare()
        newer = self.repository / "notes.txt"
        newer.write_text("Keep my work\n")
        self.reopen()
        with self.assertRaisesRegex(WorkspaceError, "newer work"):
            self.drive()
        self.assertEqual(newer.read_text(), "Keep my work\n")
        self.no_jobs()

    def test_git_tampering_blocks_restore_but_status_and_stop_still_work(self):
        self.setup_loop()

        def tamper(context, directory):
            with (directory / ".git" / "config").open("a") as stream:
                stream.write("\n[alias]\n  custom = status\n")

        self.loop.candidates.proposer_factory = lambda: MutationProposer(tamper)
        with self.assertRaises(WorkspaceError):
            self.prepare()
        with self.assertRaisesRegex(WorkspaceError, "Git metadata changed"):
            self.drive()
        status = inspect_run(WORKSPACE, self.run_root, stop=True)
        self.assertIn("HUMAN_STOP", status["research_state"]["blocking_reasons"])
        self.assertIn("custom", (self.repository / ".git" / "config").read_text())

    def test_abrupt_edit_with_no_final_inventory_is_not_erased(self):
        self.setup_loop()
        parent = self.git.prepare_parent(self.ledger.best().git_commit,
                                         known_commits={self.ledger.best().git_commit})
        preparation = self.journal.begin("EXP-000", parent.commit)
        self.journal.update(preparation.attempt_id, "EDITING", checkpoint={"parent": asdict(parent)})
        (self.repository / "labeller.py").write_text("UNRECORDED = 1\n")
        with self.assertRaisesRegex(WorkspaceError, "no recorded final snapshot"):
            self.drive()
        self.assertEqual((self.repository / "labeller.py").read_text(), "UNRECORDED = 1\n")
        self.assertEqual(len(self.ledger.history()), 1)

    def test_interrupted_restoration_resumes_after_some_recorded_paths_are_removed(self):
        self.setup_loop(max_attempts=1)
        original = (self.repository / "labeller.py").read_bytes()

        def invalid(context, directory):
            (directory / "labeller.py").write_text("invalid Python!\n")
            (directory / "configs" / "extra.json").write_text("{}\n")

        self.loop.candidates.proposer_factory = lambda: MutationProposer(invalid)
        with self.assertRaises(PreflightError):
            self.prepare()
        preparation = self.journal.latest()
        with RunLock(self.run_root):
            archive_failed_edits(self.git, preparation.checkpoint, self.latest_attempt())
            self.journal.update(preparation.attempt_id, "RECOVERING")
            (self.repository / "configs" / "extra.json").unlink()
        self.reopen()
        self.drive()
        self.assertEqual((self.repository / "labeller.py").read_bytes(), original)
        self.assertTrue((self.latest_attempt() / "rejected_files" / "configs" / "extra.json").is_file())
        self.assertEqual(self.journal.latest().stage, "RECOVERED")
        self.no_jobs()


class ControlTests(LoopFixture):
    def test_stop_before_submission_is_persistent_and_never_cleared_by_resume(self):
        self.setup_loop(max_experiments=1)
        self.drive(steps=1)
        inspect_run(WORKSPACE, self.run_root, stop=True)
        self.reopen()
        status = self.drive()
        self.assertEqual(self.ledger.get("EXP-001").state, "PREPARED")
        self.assertEqual(status["controller"]["state"], "STOPPED")
        self.no_jobs()

    def test_stop_during_job_collects_its_result_without_another_candidate(self):
        self.setup_loop()
        self.drive(steps=2)
        inspect_run(WORKSPACE, self.run_root, stop=True)
        self.reopen()
        self.drive()
        self.assertEqual(self.ledger.get("EXP-001").decision, "KEEP")
        self.assertEqual(len(self.ledger.history()), 2)

    def test_stop_during_editor_restores_changes_without_reserving_or_executing(self):
        self.setup_loop()
        original = (self.repository / "labeller.py").read_bytes()

        def edit_and_stop(context, directory):
            (directory / "labeller.py").write_text("WEIGHT_DECAY = 1.0\n")
            inspect_run(WORKSPACE, self.run_root, stop=True)

        self.loop.candidates.proposer_factory = lambda: MutationProposer(edit_and_stop)
        self.drive()
        self.assertEqual((self.repository / "labeller.py").read_bytes(), original)
        self.assertEqual(len(self.ledger.history()), 1)
        self.no_jobs()

    def test_cancelled_editor_can_be_recovered_after_restart(self):
        self.setup_loop(max_attempts=1)

        def cancel(context, directory):
            (directory / "labeller.py").write_text("WEIGHT_DECAY = 1.0\n")
            raise asyncio.CancelledError()

        self.loop.candidates.proposer_factory = lambda: MutationProposer(cancel)
        with self.assertRaises(asyncio.CancelledError):
            self.drive()
        self.assertEqual(self.journal.loop_status()["state"], "INTERRUPTED")
        self.reopen()
        self.drive()
        self.git.verify_clean(self.ledger.best().git_commit)
        self.assertEqual(self.journal.latest().failure_type, "INTERRUPTED")

    def test_known_stale_marker_is_cleared_only_after_acquiring_run_lock(self):
        self.setup_loop(max_experiments=0)
        marker = self.run_root / "candidate.lock"
        marker.write_text(json.dumps({"version": 1, "kind": "candidate", "run_root": str(self.run_root)}))
        with RunLock(self.run_root):
            with self.assertRaisesRegex(WorkspaceError, "Another controller"):
                self.drive()
            self.assertTrue(marker.exists())
            inspect_run(WORKSPACE, self.run_root)
        self.drive()
        self.assertFalse(marker.exists())
        marker.write_text('{}')
        with self.assertRaisesRegex(WorkspaceError, "Unknown candidate lock"):
            self.drive()
        self.assertTrue(marker.exists())

    def test_process_exit_releases_lock_without_deleting_lock_file(self):
        env = {**os.environ, "PYTHONPATH": str(WORKSPACE / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        script = ("import os, sys; from pathlib import Path; "
                  "from research_intern.workspace.lock import RunLock; "
                  "lock = RunLock(Path(sys.argv[1])); lock.__enter__(); "
                  "print('locked', flush=True); sys.stdin.readline(); os._exit(0)")
        process = subprocess.Popen([sys.executable, "-c", script, str(self.root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=env)
        try:
            self.assertEqual(process.stdout.readline().strip(), "locked")
            with self.assertRaises(WorkspaceError):
                with RunLock(self.root):
                    self.fail("concurrent controller acquired lock")
            process.communicate("exit\n", timeout=10)
            self.assertEqual(process.returncode, 0)
            with RunLock(self.root):
                self.assertTrue((self.root / "operation.lock").exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_cli_status_stop_resume_and_invalid_paths(self):
        self.setup_loop(max_experiments=0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["status", str(self.run_root), "--json"]), 0)
        status = json.loads(output.getvalue())
        self.assertEqual(status["mode"], "simulated")
        self.assertEqual(status["copilot_usage"], 0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["stop", str(self.run_root)]), 0)
            self.assertEqual(main(["resume", str(self.run_root)]), 0)
        self.assertTrue(self.ledger.snapshot().stop_requested)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["status", str(WORKSPACE)]), 1)
            self.assertEqual(main(["resume", str(self.root / "missing")]), 1)
        with patch("research_intern.offline.initialize_run", side_effect=AssertionError("created invalid run")):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["run", "--steps", "0"])
