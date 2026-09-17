"""One offline candidate: persisted context → edit → validate → commit → reserve."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from research_intern.contracts.loader import load_contract
from research_intern.controller.handoff import HandoffBlocked, build_iteration_context
from research_intern.copilot.proposer import Proposer
from research_intern.domain.experiments import Candidate, ExperimentRecord, SliceError
from research_intern.domain.research import CandidatePlan
from research_intern.ledger.research_state import build_research_state
from research_intern.ledger.sqlite import Ledger
from research_intern.ledger.preparations import Preparation, PreparationJournal
from research_intern.execution.outputs import read_json
from research_intern.validation.preflight import PreflightError, validate_files
from research_intern.workspace.git import GitWorkspace, PermissionViolation, WorkspaceError
from research_intern.workspace.paths import child_path
from research_intern.workspace.lock import RunLock, own_run
from research_intern.workspace.recovery import atomic_bytes, capture_failure


def write_evidence(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError(path)
    atomic_bytes(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def ensure_evidence(path: Path, value: dict) -> None:
    if path.exists():
        if read_json(path) != json.loads(json.dumps(value, allow_nan=False)):
            raise WorkspaceError(f"Persisted evidence changed: {path.name}")
    else:
        write_evidence(path, value)


class CandidateController:
    def __init__(self, ledger: Ledger, workspace: GitWorkspace,
                 proposer_factory: Callable[[], Proposer], *, timeout_seconds: float = 30):
        if (ledger.contract is None or ledger.repository != workspace.repository
                or ledger.root != workspace.run_root):
            raise WorkspaceError("Candidate preparation requires a persisted contract and matching dedicated repository")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("The proposal timeout must be finite and positive")
        self.ledger = ledger
        self.workspace = workspace
        self.proposer_factory = proposer_factory
        self.timeout_seconds = timeout_seconds
        self.journal = PreparationJournal(ledger)

    async def prepare_candidate(self, *, owner: RunLock | None = None) -> ExperimentRecord:
        """Return PREPARED; the existing execution controller submits/polls later.

        Invalid attempts retain their diagnostics and workspace changes. They do
        not reserve an experiment ID, spend an experiment slot, or invoke execution.
        """
        with own_run(self.ledger.root, owner):
            lock = child_path(self.ledger.root, "candidate.lock")
            try:
                write_evidence(lock, {"version": 1, "kind": "candidate", "run_root": str(self.ledger.root)})
            except FileExistsError as exc:
                raise WorkspaceError("Candidate preparation is already active or needs interruption reconciliation") from exc
            try:
                return await self._prepare()
            finally:
                lock.unlink()

    async def _prepare(self) -> ExperimentRecord:
        source = self.ledger.snapshot()
        state = build_research_state(source)
        context = replace(build_iteration_context(state), contract=self.ledger.contract, task=(
            "Inspect the prepared selected-parent code and recorded evidence. Make one intervention "
            "only within the immutable contract's editable scope and return a concise CandidatePlan. "
            "Do not edit protected files, Git metadata, policy, budgets, or outputs. "
            "Do not assign IDs, commit, execute experiments, or judge success. This is an offline mock iteration."
        ))
        # Refuse unexpected work before allocating a preparation or moving HEAD.
        self.workspace.verify_clean()
        if self.workspace.head not in {r.git_commit for r in source.experiments}:
            raise WorkspaceError("Unrecorded Git history requires reconciliation before another candidate")
        preparation = self.journal.begin(context.selected_parent.experiment_id, context.selected_parent.git_commit)
        context = replace(context, attempt_number=preparation.sequence, preparation_failures=self.journal.failures())
        attempts = child_path(self.ledger.root, "candidate_attempts")
        attempts.mkdir(exist_ok=True)
        attempt = child_path(attempts, preparation.attempt_id)
        attempt.mkdir()
        stage = "WORKSPACE_FAILED"
        checkpoint = {}
        try:
            parent = self.workspace.prepare_parent(context.selected_parent.git_commit,
                                                   known_commits={r.git_commit for r in source.experiments})
            if load_contract(self.workspace.repository) != self.ledger.contract:
                raise WorkspaceError("The selected parent's contract differs from the persisted research boundary")
            checkpoint = {"parent": asdict(parent), "source_revision": source.revision}
            self.journal.update(preparation.attempt_id, "EDITING", checkpoint=checkpoint)
            write_evidence(attempt / "context.json", {**asdict(context), "contract": context.contract.to_dict()})
            if self.ledger.snapshot() != source:
                raise HandoffBlocked("Research history or stop state changed before proposal generation")
            stage = "PROPOSER_FAILED"
            proposer = self.proposer_factory()
            if proposer.backend != "simulated":
                raise HandoffBlocked("The candidate workflow does not enable live proposers")
            plan = await asyncio.wait_for(proposer.run_iteration(context, self.workspace.repository),
                                          timeout=self.timeout_seconds)
            stage = "CANDIDATE_INVALID"
            if not isinstance(plan, CandidatePlan) or plan.parent_experiment != context.selected_parent.experiment_id:
                raise PreflightError("The candidate plan must identify the controller-selected parent")
            write_evidence(attempt / "plan.json", asdict(plan))
            if self.ledger.snapshot() != source:
                raise HandoffBlocked("Research history or stopping state changed; discard this stale candidate")
            files, changed = self.workspace.inspect_changes(parent, self.ledger.contract)
            checks = validate_files(self.workspace.repository, changed)
            validation = {"status": "PREFLIGHT_PASSED", "changed_paths": list(changed), "checks": list(checks)}
            write_evidence(attempt / "validation.json", validation)
            if self.ledger.snapshot() != source:
                raise HandoffBlocked("Research history or stopping state changed during validation")
            stage = "GIT_FAILED"
            checkpoint.update(validated_files={name: asdict(entry) for name, entry in files.items()},
                              plan=asdict(plan), validation=validation)
            self.journal.update(preparation.attempt_id, "COMMITTING", checkpoint=checkpoint)
            commit, diff = self.workspace.commit_candidate(parent, self.ledger.contract, files, plan.parent_experiment)
            checkpoint.update(git_commit=commit, diff=diff)
            preparation = self.journal.update(preparation.attempt_id, "COMMITTED", checkpoint=checkpoint)
            stage = "RESERVATION_BLOCKED"
            if self.ledger.snapshot() != source:
                raise HandoffBlocked("Research state changed before reservation; the committed candidate is retained")
            return self.reserve_preparation(preparation)
        except BaseException as exc:
            category = "PERMISSION_VIOLATION" if isinstance(exc, PermissionViolation) else stage
            if isinstance(exc, asyncio.CancelledError):
                category = "INTERRUPTED"
            saved = self.journal.get(preparation.attempt_id)
            if saved.stage in ("PREPARING", "EDITING"):
                if saved.stage == "EDITING":
                    checkpoint = capture_failure(self.workspace, saved.checkpoint)
                self.journal.update(preparation.attempt_id, "FAILED", checkpoint=checkpoint,
                                    failure_type=category, failure_message=str(exc) or type(exc).__name__)
            try:
                write_evidence(attempt / "failure.json", {"category": category, "message": str(exc) or type(exc).__name__,
                                                         "workspace_changes": "retained for inspection"})
            except OSError as evidence_error:
                exc.add_note(f"Could not persist candidate failure diagnostics: {evidence_error}")
            if isinstance(exc, TimeoutError):
                raise SliceError("Offline proposer timed out; diagnostics and edits were retained") from exc
            raise

    def reserve_preparation(self, preparation: Preparation) -> ExperimentRecord:
        checkpoint = preparation.checkpoint
        commit, diff = checkpoint["git_commit"], checkpoint["diff"]
        plan = CandidatePlan(**checkpoint["plan"])
        attempt = child_path(self.ledger.root, "candidate_attempts", preparation.attempt_id)
        evidence = {"mode": "simulated", "parent_commit": preparation.parent_commit, "git_commit": commit,
                    "contract": self.ledger.contract.to_dict(), "plan": asdict(plan),
                    "validation": checkpoint["validation"], "attempt": attempt.relative_to(self.ledger.root).as_posix()}
        candidates = child_path(self.ledger.root, "candidates")
        candidates.mkdir(exist_ok=True)
        ensure_evidence(child_path(candidates, f"{commit}.json"), evidence)
        diff_path = child_path(attempt, "diff.patch")
        if diff_path.exists() and diff_path.read_bytes() != diff.encode("utf-8"):
            raise WorkspaceError("The preparation's diff evidence changed")
        if not diff_path.exists():
            atomic_bytes(diff_path, diff.encode("utf-8"))
        if preparation.stage == "RESERVED":
            record = self.ledger.get(preparation.experiment_id)
        else:
            record = self.ledger.reserve(Candidate(plan.parent_experiment, commit, plan.hypothesis,
                                                  plan.planned_intervention, diff), preparation_id=preparation.attempt_id)
        ensure_evidence(self.ledger.directory(record.experiment_id) / "candidate.json",
                        {**evidence, "experiment_id": record.experiment_id})
        ensure_evidence(attempt / "result.json", {"status": "PREPARED", "experiment_id": record.experiment_id,
                                                "git_commit": commit})
        return record
