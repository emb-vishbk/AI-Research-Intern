"""Serial offline research orchestration with bounded preparation and recovery."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import asdict

from research_intern.contracts.loader import load_contract
from research_intern.controller.candidate import CandidateController
from research_intern.controller.execution import ExecutionController
from research_intern.domain.experiments import JobRequest, SliceError
from research_intern.domain.research import continuation_reasons
from research_intern.execution.outputs import read_json
from research_intern.execution.simulated import SimulatedExecutor
from research_intern.ledger.research_state import build_research_state
from research_intern.ledger.preparations import PreparationJournal
from research_intern.ledger.sqlite import Ledger, LedgerError
from research_intern.workspace.git import WorkspaceError
from research_intern.workspace.lock import RunLock, own_run
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import (
    archive_failed_edits, finish_commit, restore_failed_edits, verify_recovery,
)


class ResearchLoop:
    def __init__(self, candidates: CandidateController, execution: ExecutionController,
                 *, emit: Callable[[str], None] = lambda message: None):
        if candidates.ledger is not execution.ledger or not isinstance(execution.executor, SimulatedExecutor):
            raise SliceError("The offline loop requires one shared ledger and a simulated executor")
        self.candidates, self.execution = candidates, execution
        self.ledger, self.workspace = candidates.ledger, candidates.workspace
        self.journal = candidates.journal
        self.policy = self.journal.policy()
        if self.policy is None:
            raise SliceError("This run has no persisted offline loop policy")
        self.emit = emit

    def _clear_stale_marker(self) -> None:
        marker = child_path(self.ledger.root, "candidate.lock")
        if marker.exists():
            expected = {"version": 1, "kind": "candidate", "run_root": str(self.ledger.root)}
            if read_json(marker) != expected:
                raise WorkspaceError("Unknown candidate lock requires manual reconciliation")
            # Caller holds the process lock, so no managed candidate is active.
            marker.unlink()

    def _recover_preparation(self) -> None:
        self._clear_stale_marker()
        preparation = self.journal.latest()
        if preparation is None or preparation.stage == "RECOVERED":
            return
        attempt = child_path(self.ledger.root, "candidate_attempts", preparation.attempt_id)
        attempt.mkdir(parents=True, exist_ok=True)
        if preparation.stage == "PREPARING":
            self.workspace.verify_clean()
            if self.workspace.head not in {r.git_commit for r in self.ledger.history()}:
                raise WorkspaceError("Interrupted parent preparation left unrecorded Git state")
            preparation = self.journal.update(preparation.attempt_id, "FAILED", failure_type="INTERRUPTED",
                                              failure_message="Interrupted before editing began")
        elif preparation.stage == "EDITING":
            # An abrupt process death has no authoritative post-edit inventory.
            # Only an unchanged workspace can be retried without human review.
            verify_recovery(self.workspace, preparation.checkpoint)
            preparation = self.journal.update(preparation.attempt_id, "FAILED", failure_type="INTERRUPTED",
                                              failure_message="Interrupted before a candidate was validated")
        if preparation.stage == "FAILED":
            if "parent" in preparation.checkpoint:
                archive_failed_edits(self.workspace, preparation.checkpoint, attempt)
                preparation = self.journal.update(preparation.attempt_id, "RECOVERING")
            else:
                self.workspace.verify_clean()
                if (self.workspace.head not in {r.git_commit for r in self.ledger.history()}
                        or load_contract(self.workspace.repository) != self.ledger.contract):
                    raise WorkspaceError("Failed setup requires manual reconciliation")
                self.journal.update(preparation.attempt_id, "RECOVERED")
                return
        if preparation.stage == "RECOVERING":
            restore_failed_edits(self.workspace, preparation.checkpoint, attempt)
            self.journal.update(preparation.attempt_id, "RECOVERED")
            self.emit(f"Recovered failed preparation {preparation.sequence}; evidence retained.")
        elif preparation.stage in ("COMMITTING", "COMMITTED"):
            if continuation_reasons(self.ledger.snapshot()):
                return  # A human stop or exhausted budget never authorizes reservation.
            if (self.ledger.best().experiment_id != preparation.parent_experiment
                    or self.ledger.snapshot().revision != preparation.checkpoint["source_revision"]):
                raise WorkspaceError("Research history changed before candidate reservation")
            commit, diff = finish_commit(self.workspace, preparation.checkpoint, self.ledger.contract,
                                         preparation.parent_experiment)
            if preparation.stage == "COMMITTING":
                checkpoint = {**preparation.checkpoint, "git_commit": commit, "diff": diff}
                preparation = self.journal.update(preparation.attempt_id, "COMMITTED", checkpoint=checkpoint)
            record = self.candidates.reserve_preparation(preparation)
            self.emit(f"Recovered {record.experiment_id} from its validated candidate commit.")
        elif preparation.stage == "RESERVED":
            # Reservation and journal linkage are one transaction. Finish any
            # interrupted artifact publication without reserving another ID.
            self.candidates.reserve_preparation(preparation)

    async def step(self, *, owner: RunLock | None = None) -> bool:
        """Advance one preparation or job transition. False means a hard stop."""
        with own_run(self.ledger.root, owner) as lock:
            self._recover_preparation()
            snapshot = self.ledger.snapshot()
            pending = [record for record in snapshot.experiments if not record.terminal]
            if len(pending) > 1:
                raise SliceError("Multiple unfinished experiments require reconciliation")
            if pending:
                record = pending[0]
                if record.state == "PREPARED":
                    if snapshot.stop_requested:
                        self.journal.note_loop("STOPPED", "HUMAN_STOP; prepared candidate was not submitted")
                        return False
                    try:
                        record = self.execution.resume_submission(record.experiment_id)
                    except LedgerError:
                        if self.ledger.snapshot().stop_requested and self.ledger.get(record.experiment_id).state == "PREPARED":
                            self.journal.note_loop("STOPPED", "HUMAN_STOP; prepared candidate was not submitted")
                            return False
                        raise
                elif record.state == "SUBMITTING":
                    request = JobRequest(record.experiment_id, record.parent_experiment, record.git_commit)
                    job_id = self.execution.executor.find_job(request)
                    if job_id is None:
                        # Only the simulator can prove this from local durable job
                        # files. Do not resubmit an ambiguous real cloud request.
                        record = self.ledger.record_failure(record.experiment_id, "SUBMISSION_FAILED",
                                                            "Interrupted submission has no durable simulated job; no resubmission attempted")
                    else:
                        self.ledger.record_job(record.experiment_id, job_id)
                        record = self.ledger.get(record.experiment_id)
                else:
                    record = self.execution.advance(record.experiment_id)
                self.emit(f"SIMULATED {record.experiment_id}: {record.state}" +
                          (f" / {record.decision}" if record.decision else ""))
                return True
            reasons = continuation_reasons(snapshot)
            if not reasons and self.journal.count() >= self.policy["max_attempts"]:
                reasons = ("PREPARATION_BUDGET_EXHAUSTED",)
            if reasons:
                preparation = self.journal.latest()
                # Preserve an interrupted unreserved commit for inspection. A
                # normal completed run ends with the best recorded code checked out.
                if preparation is None or preparation.stage in ("RESERVED", "RECOVERED"):
                    best = self.ledger.best()
                    self.workspace.prepare_parent(best.git_commit, known_commits={r.git_commit for r in snapshot.experiments})
                self.journal.note_loop("STOPPED", ", ".join(reasons))
                return False
            try:
                record = await self.candidates.prepare_candidate(owner=lock)
            except Exception:
                latest = self.journal.latest()
                if latest is None or latest.stage != "FAILED":
                    if self.ledger.snapshot().stop_requested:
                        return True
                    raise
                self._recover_preparation()
                return True
            self.emit(f"Prepared {record.experiment_id} from {record.parent_experiment}.")
            return True

    async def run(self, *, steps: int | None = None, poll_interval: float = 0.1) -> dict:
        if steps is not None and (type(steps) is not int or steps < 1):
            raise ValueError("steps must be a positive integer")
        if not math.isfinite(poll_interval) or poll_interval < 0:
            raise ValueError("poll_interval must be finite and non-negative")
        with own_run(self.ledger.root) as lock:
            self.journal.note_loop("RUNNING")
            try:
                completed = 0
                while await self.step(owner=lock):
                    completed += 1
                    if steps is not None and completed >= steps:
                        self.journal.note_loop("PAUSED", "Requested step limit reached; resume continues this run")
                        break
                    await asyncio.sleep(poll_interval)
            except (asyncio.CancelledError, KeyboardInterrupt):
                self.journal.note_loop("INTERRUPTED", "Resume will reconcile persisted preparation and job state")
                raise
            except Exception as exc:
                self.journal.note_loop("RECOVERY_REQUIRED", str(exc))
                raise
            return self.status()

    def status(self) -> dict:
        return describe_run(self.ledger)


def describe_run(ledger: Ledger) -> dict:
    """Status and stop remain usable even when the experiment workspace needs repair."""
    journal = PreparationJournal(ledger)
    policy = journal.policy()
    if policy is None:
        raise SliceError("The selected directory is not an initialized offline loop run")
    preparation = journal.latest()
    count = journal.count()
    return {"mode": "simulated", "run_directory": str(ledger.root),
            "controller": journal.loop_status(),
            "research_state": asdict(build_research_state(ledger.snapshot())),
            "preparation_budget": {"limit": policy["max_attempts"], "used": count,
                                   "remaining": max(0, policy["max_attempts"] - count)},
            "last_preparation": None if preparation is None else {
                "attempt_id": preparation.attempt_id, "sequence": preparation.sequence, "stage": preparation.stage,
                "failure_type": preparation.failure_type, "failure_message": preparation.failure_message},
            "copilot_usage": 0, "azure_gpu_hours": 0}
