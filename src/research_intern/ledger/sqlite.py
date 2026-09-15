"""Persistent, serial bookkeeping for one explicitly simulated research run."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from research_intern.contracts.models import ExperimentContract, OutputPaths

from research_intern.domain.experiments import (
    Candidate, CollectedResult, Constraint, Evaluation, EvaluationRules, ExperimentRecord, SliceError,
)
from research_intern.domain.research import (
    ResearchSnapshot, continuation_reasons, validate_experiment_limit,
)
from research_intern.workspace.paths import child_path


class LedgerError(SliceError):
    """A ledger invariant or lifecycle transition was violated."""


def encode(value: object) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _check_commit(commit: str) -> None:
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise LedgerError("Expected a full Git commit ID from the validated candidate")


class Ledger:
    """One database per simulation; live adapters are deliberately not enabled."""

    @classmethod
    def reopen(cls, root: Path) -> Ledger:
        """Load fixed rules from an existing ledger; never create a missing run."""
        root = root.resolve(strict=True)
        path = child_path(root, "ledger.sqlite3")
        if not path.is_file():
            raise LedgerError("The selected run has no existing ledger")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = 'evaluation_rules'").fetchone()
            if row is None:
                raise LedgerError("The existing ledger has no evaluation rules")
            data = json.loads(row[0])
        data["constraints"] = tuple(Constraint(**item) for item in data["constraints"])
        return cls(root, EvaluationRules(**data))

    def __init__(self, root: Path, rules: EvaluationRules, *, max_experiments: int | None = None,
                 contract: ExperimentContract | None = None, repository: Path | None = None):
        if contract is not None:
            contract = ExperimentContract.from_dict(contract.to_dict())
            if contract.rules != rules or max_experiments not in (None, contract.budget.max_experiments):
                raise LedgerError("The contract must agree with ledger evaluation rules and budget")
            max_experiments = contract.budget.max_experiments
            if repository is None:
                raise LedgerError("A full contract requires an explicit dedicated repository")
        elif repository is not None:
            raise LedgerError("Repository binding requires a full contract")
        if max_experiments is not None:
            validate_experiment_limit(max_experiments)
        self.root = root.resolve(strict=True)
        self.rules = rules
        self.path = child_path(self.root, "ledger.sqlite3")
        self._db = sqlite3.connect(self.path, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA temp_store = MEMORY")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    sequence INTEGER UNIQUE NOT NULL,
                    parent_experiment TEXT REFERENCES experiments(experiment_id),
                    parent_commit TEXT,
                    git_commit TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    planned_intervention TEXT NOT NULL,
                    diff TEXT NOT NULL,
                    backend TEXT NOT NULL CHECK (backend = 'simulated'),
                    state TEXT NOT NULL,
                    job_id TEXT UNIQUE,
                    job_status TEXT,
                    decision TEXT,
                    score REAL,
                    evaluation_json TEXT,
                    metrics_json TEXT,
                    failure_type TEXT,
                    failure_message TEXT,
                    outputs_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
                    state TEXT NOT NULL,
                    job_id TEXT,
                    job_status TEXT,
                    timestamp TEXT NOT NULL
                );
            """)
            with self._transaction():
                for key, value in (("schema_version", "1"), ("mode", "simulated"),
                                   ("evaluation_rules", encode(asdict(rules)))):
                    self._db.execute("INSERT OR IGNORE INTO metadata VALUES (?, ?)", (key, value))
                    if self._db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()[0] != value:
                        raise LedgerError("Ledger mode, schema, or evaluation rules cannot change")
                saved = self._experiment_limit()
                if max_experiments is not None and saved is None:
                    if self._db.execute("SELECT 1 FROM experiments LIMIT 1").fetchone():
                        raise LedgerError("Set the experiment budget before importing the baseline; use a new run for legacy history")
                    self._db.execute("INSERT INTO metadata VALUES ('max_experiments', ?)",
                                     (encode(max_experiments),))
                elif max_experiments is not None and saved != max_experiments:
                    raise LedgerError("The persisted experiment budget cannot change")
                row = self._db.execute("SELECT value FROM metadata WHERE key = 'experiment_contract'").fetchone()
                if contract is not None and row is None:
                    if self._db.execute("SELECT 1 FROM experiments LIMIT 1").fetchone():
                        raise LedgerError("Bind the contract before baseline import; use a new run for legacy history")
                    self._db.execute("INSERT INTO metadata VALUES ('experiment_contract', ?)",
                                     (encode(contract.to_dict()),))
                    self._db.execute("INSERT INTO metadata VALUES ('experiment_repository', ?)",
                                     (str(repository.resolve(strict=True)),))
                elif contract is not None and row[0] != encode(contract.to_dict()):
                    raise LedgerError("The effective experiment contract cannot change within a run")
                row = self._db.execute("SELECT value FROM metadata WHERE key = 'experiment_contract'").fetchone()
                self.contract = ExperimentContract.from_dict(json.loads(row[0])) if row else None
                row = self._db.execute("SELECT value FROM metadata WHERE key = 'experiment_repository'").fetchone()
                self.repository = Path(row[0]) if row else None
                if repository is not None and self.repository != repository.resolve(strict=True):
                    raise LedgerError("The dedicated experiment repository cannot change within a run")
                if self.contract is not None and (
                    self.contract.rules != self.rules or self.contract.budget.max_experiments != self._experiment_limit()
                    or self.repository is None
                ):
                    raise LedgerError("The persisted contract disagrees with the run configuration")
                self.output_paths = self.contract.outputs if self.contract else OutputPaths()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _transaction(self, *, immediate: bool = True) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield
        except BaseException:
            self._db.rollback()
            raise
        else:
            self._db.commit()

    def _event(self, experiment_id: str) -> None:
        self._db.execute("""
            INSERT INTO events (experiment_id, state, job_id, job_status, timestamp)
            SELECT experiment_id, state, job_id, job_status, updated_at
            FROM experiments WHERE experiment_id = ?
        """, (experiment_id,))

    def get(self, experiment_id: str) -> ExperimentRecord:
        row = self._db.execute("SELECT * FROM experiments WHERE experiment_id = ?",
                               (experiment_id,)).fetchone()
        if row is None:
            raise LedgerError("Unknown experiment ID")
        data = dict(row)
        data.pop("score")
        value = data.pop("evaluation_json")
        if value is not None:
            value = json.loads(value)
            value["constraint_results"] = tuple(value["constraint_results"])
            value = Evaluation(**value)
        data["evaluation"] = value
        metrics = data.pop("metrics_json")
        data["metrics"] = json.loads(metrics) if metrics is not None else None
        return ExperimentRecord(**data)

    def recent_events(self, limit: int = 100) -> list[dict]:
        """Persisted lifecycle evidence in ascending order, bounded for the UI."""
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Event limit must be between 1 and 500")
        rows = self._db.execute("SELECT * FROM events ORDER BY sequence DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def history(self) -> list[ExperimentRecord]:
        return [self.get(row[0]) for row in self._db.execute(
            "SELECT experiment_id FROM experiments ORDER BY sequence"
        ).fetchall()]

    def _experiment_limit(self) -> int | None:
        row = self._db.execute("SELECT value FROM metadata WHERE key = 'max_experiments'").fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
            validate_experiment_limit(value)
        except (ValueError, TypeError) as exc:
            raise LedgerError("The persisted experiment budget is invalid") from exc
        return value

    def _snapshot(self) -> ResearchSnapshot:
        stopped = self._db.execute("SELECT value FROM metadata WHERE key = 'stop_requested'").fetchone()
        if stopped is not None and stopped[0] != "true":
            raise LedgerError("Invalid persisted stop request")
        revision = self._db.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0]
        return ResearchSnapshot(self.rules, tuple(self.history()), self._experiment_limit(),
                                stopped is not None, revision)

    def snapshot(self) -> ResearchSnapshot:
        """Read history and control metadata from the same SQLite transaction."""
        with self._transaction(immediate=False):
            return self._snapshot()

    def request_stop(self) -> None:
        """Persist a human stop for this run. Existing jobs may still be collected."""
        with self._transaction():
            self._db.execute("INSERT OR IGNORE INTO metadata VALUES ('stop_requested', 'true')")

    def best(self) -> ExperimentRecord:
        order = "DESC" if self.rules.direction == "maximize" else "ASC"
        row = self._db.execute(f"""
            SELECT experiment_id FROM experiments
            WHERE state = 'RECORDED' AND decision IN ('KEEP', 'GOAL_REACHED')
            ORDER BY score {order}, sequence ASC LIMIT 1
        """).fetchone()
        if row is None:
            raise LedgerError("An evaluable baseline must be recorded first")
        return self.get(row[0])

    def directory(self, experiment_id: str) -> Path:
        if not re.fullmatch(r"EXP-[0-9]{3,}", experiment_id):
            raise LedgerError("Invalid experiment ID for an artifact directory")
        path = child_path(self.root, "experiments", experiment_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def outputs(self, experiment_id: str) -> Path:
        path = child_path(self.directory(experiment_id), self.output_paths.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _relative_outputs(self, outputs: Path) -> str:
        try:
            relative = outputs.relative_to(self.root)
            checked = child_path(self.root, str(relative))
            if not checked.is_dir():
                raise LedgerError("The output directory does not exist")
            return relative.as_posix()
        except ValueError as exc:
            raise LedgerError("Evidence must remain inside this ledger's directory") from exc

    def import_baseline(self, commit: str, result: CollectedResult, evaluation: Evaluation) -> ExperimentRecord:
        _check_commit(commit)
        path = self._relative_outputs(self.outputs("EXP-000"))
        if not evaluation.constraints_satisfied or evaluation.decision not in ("KEEP", "GOAL_REACHED"):
            raise LedgerError("The baseline must be valid and eligible")
        with self._transaction():
            if self._db.execute("SELECT 1 FROM experiments LIMIT 1").fetchone():
                raise LedgerError("A baseline is already present; it cannot be overwritten")
            timestamp = _now()
            self._db.execute("""
                INSERT INTO experiments (
                    experiment_id, sequence, git_commit, hypothesis, planned_intervention,
                    diff, backend, state, decision, score, evaluation_json, metrics_json,
                    outputs_path, created_at, updated_at
                ) VALUES ('EXP-000', 0, ?, 'Synthetic baseline', 'Import prepared outputs',
                          '', 'simulated', 'RECORDED', ?, ?, ?, ?, ?, ?, ?)
            """, (commit, evaluation.decision, result.score, encode(asdict(evaluation)),
                  encode(result.metrics), path, timestamp, timestamp))
            self._event("EXP-000")
        return self.get("EXP-000")

    def reserve(self, candidate: Candidate, *, preparation_id: str | None = None) -> ExperimentRecord:
        _check_commit(candidate.git_commit)
        if not all(value.strip() for value in (candidate.hypothesis, candidate.planned_intervention, candidate.diff)):
            raise LedgerError("A candidate requires a hypothesis, intervention, and actual diff")
        with self._transaction():
            if preparation_id is None and self._db.execute("SELECT 1 FROM metadata WHERE key = 'loop_policy'").fetchone():
                raise LedgerError("Offline loop candidates require a journaled preparation")
            if preparation_id is not None:
                preparation = self._db.execute("SELECT * FROM preparations WHERE attempt_id = ?", (preparation_id,)).fetchone()
                if preparation is None or preparation["stage"] != "COMMITTED":
                    raise LedgerError("Reservation requires one committed, unreserved preparation")
                checkpoint = json.loads(preparation["checkpoint_json"])
                if (preparation["parent_experiment"] != candidate.parent_experiment
                        or checkpoint.get("git_commit") != candidate.git_commit):
                    raise LedgerError("Candidate does not match its preparation journal")
            reasons = continuation_reasons(self._snapshot())
            if reasons:
                raise LedgerError("Cannot reserve a candidate: " + ", ".join(reasons))
            parent = self.get(candidate.parent_experiment)
            if parent.decision not in ("KEEP", "GOAL_REACHED") or parent.state != "RECORDED":
                raise LedgerError("The selected parent must be an eligible recorded experiment")
            if candidate.git_commit == parent.git_commit:
                raise LedgerError("The candidate must differ from its parent code state")
            number = self._db.execute("SELECT MAX(sequence) + 1 FROM experiments").fetchone()[0]
            experiment_id = f"EXP-{number:03d}"
            timestamp = _now()
            self._db.execute("""
                INSERT INTO experiments (
                    experiment_id, sequence, parent_experiment, parent_commit, git_commit,
                    hypothesis, planned_intervention, diff, backend, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'simulated', 'PREPARED', ?, ?)
            """, (experiment_id, number, parent.experiment_id, parent.git_commit, candidate.git_commit,
                  candidate.hypothesis, candidate.planned_intervention, candidate.diff, timestamp, timestamp))
            self._event(experiment_id)
            if preparation_id is not None:
                self._db.execute("UPDATE preparations SET stage = 'RESERVED', experiment_id = ?, updated_at = ? WHERE attempt_id = ?",
                                 (experiment_id, timestamp, preparation_id))
        return self.get(experiment_id)

    def _update(self, experiment_id: str, expected: tuple[str, ...], **values: object) -> None:
        # Column names are private call-site constants, never external input.
        with self._transaction():
            if self.get(experiment_id).state not in expected:
                raise LedgerError("Illegal experiment state transition")
            values["updated_at"] = _now()
            assignments = ", ".join(f"{key} = ?" for key in values)
            self._db.execute(f"UPDATE experiments SET {assignments} WHERE experiment_id = ?",
                             (*values.values(), experiment_id))
            self._event(experiment_id)

    def begin_submission(self, experiment_id: str) -> None:
        with self._transaction():
            snapshot = self._snapshot()
            if snapshot.stop_requested:
                raise LedgerError("HUMAN_STOP: the prepared candidate will not be submitted")
            if snapshot.max_experiments is None:
                raise LedgerError("BUDGET_NOT_CONFIGURED: the candidate cannot be submitted")
            if self.get(experiment_id).state != "PREPARED":
                raise LedgerError("Illegal experiment state transition")
            # Reservation already consumed the slot. Exhaustion must not prevent
            # the final permitted candidate from submitting or resuming.
            self._db.execute("UPDATE experiments SET state = 'SUBMITTING', updated_at = ? WHERE experiment_id = ?",
                             (_now(), experiment_id))
            self._event(experiment_id)

    def record_job(self, experiment_id: str, job_id: str) -> None:
        if not job_id or not job_id.startswith("simulated-"):
            raise LedgerError("This slice accepts only explicitly simulated job IDs")
        self._update(experiment_id, ("SUBMITTING",), state="SUBMITTED", job_id=job_id)

    def record_status(self, experiment_id: str, status: str) -> None:
        if status not in ("queued", "running", "completed", "failed", "cancelled"):
            raise LedgerError("Unknown execution status")
        self._update(experiment_id, ("SUBMITTED", "RUNNING"), state="RUNNING", job_status=status)

    def record_result(self, experiment_id: str, result: CollectedResult, evaluation: Evaluation) -> ExperimentRecord:
        path = self._relative_outputs(self.outputs(experiment_id))
        if self.get(experiment_id).job_status != "completed":
            raise LedgerError("Only a completed job can receive a scientific evaluation")
        self._update(experiment_id, ("RUNNING",), state="RECORDED", decision=evaluation.decision,
                     score=result.score, evaluation_json=encode(asdict(evaluation)),
                     metrics_json=encode(result.metrics), outputs_path=path)
        return self.get(experiment_id)

    def record_failure(self, experiment_id: str, failure_type: str, message: str) -> ExperimentRecord:
        outputs = self.outputs(experiment_id)
        path = self._relative_outputs(outputs) if outputs.is_dir() else None
        self._update(experiment_id, ("SUBMITTING", "SUBMITTED", "RUNNING"),
                     state="FAILED", decision="FAILED", failure_type=failure_type,
                     failure_message=message, outputs_path=path)
        return self.get(experiment_id)
