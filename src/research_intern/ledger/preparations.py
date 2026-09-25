"""Durable pre-experiment attempts and the offline loop's fixed application policy."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from research_intern.domain.research import continuation_reasons, validate_experiment_limit
from research_intern.ledger.sqlite import Ledger, LedgerError, encode, _now

LOOP_SCENARIOS = ("mixed", "goal", "runtime-failure", "invalid-first", "invalid-always", "protected-first")
ACTIVE_STAGES = ("PREPARING", "EDITING", "COMMITTING", "COMMITTED", "RECOVERING")


@dataclass(frozen=True)
class Preparation:
    attempt_id: str
    sequence: int
    stage: str
    parent_experiment: str
    parent_commit: str
    checkpoint: dict
    experiment_id: str | None
    failure_type: str | None
    failure_message: str | None


class PreparationJournal:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        ledger._db.execute("""
            CREATE TABLE IF NOT EXISTS preparations (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL UNIQUE,
                stage TEXT NOT NULL,
                parent_experiment TEXT NOT NULL REFERENCES experiments(experiment_id),
                parent_commit TEXT NOT NULL,
                checkpoint_json TEXT NOT NULL,
                experiment_id TEXT UNIQUE REFERENCES experiments(experiment_id),
                failure_type TEXT,
                failure_message TEXT,
                updated_at TEXT NOT NULL
            )
        """)

    def configure(self, *, max_attempts: int, scenario: str) -> None:
        validate_experiment_limit(max_attempts)
        if scenario not in LOOP_SCENARIOS and not (scenario == "live" and self.ledger.mode == "live"):
            raise ValueError("Unknown offline loop scenario")
        policy = {"version": 1, "max_attempts": max_attempts, "scenario": scenario}
        with self.ledger._transaction():
            saved = self.policy()
            if saved is not None and saved != policy:
                raise LedgerError("The persisted preparation limit and simulation scenario cannot change")
            if saved is None:
                if self.ledger.history() or self.count():
                    raise LedgerError("Configure the offline loop before baseline import")
                self.ledger._db.execute("INSERT INTO metadata VALUES ('loop_policy', ?)", (encode(policy),))

    def policy(self) -> dict | None:
        row = self.ledger._db.execute("SELECT value FROM metadata WHERE key = 'loop_policy'").fetchone()
        if row is None:
            return None
        policy = json.loads(row[0])
        if (not isinstance(policy, dict) or set(policy) != {"version", "max_attempts", "scenario"}
                or policy["version"] != 1 or policy["scenario"] not in (*LOOP_SCENARIOS, "live")):
            raise LedgerError("Invalid persisted offline loop policy")
        validate_experiment_limit(policy["max_attempts"])
        return policy

    def count(self) -> int:
        if self.ledger.mode == "live" and self.ledger._db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_settlements'").fetchone():
            # Retain attempt IDs/history while exempting proven pre-prompt failures.
            return self.ledger._db.execute("""SELECT COUNT(*) FROM preparations p WHERE NOT EXISTS (
                SELECT 1 FROM service_settlements s WHERE s.service='copilot' AND s.units=0
                AND s.request_id='attempt-' || p.sequence)""").fetchone()[0]
        return self.ledger._db.execute("SELECT COUNT(*) FROM preparations").fetchone()[0]

    def get(self, attempt_id: str) -> Preparation:
        row = self.ledger._db.execute("SELECT * FROM preparations WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise LedgerError("Unknown preparation attempt")
        data = dict(row)
        data.pop("updated_at")
        data["checkpoint"] = json.loads(data.pop("checkpoint_json"))
        return Preparation(**data)

    def latest(self) -> Preparation | None:
        row = self.ledger._db.execute("SELECT attempt_id FROM preparations ORDER BY sequence DESC LIMIT 1").fetchone()
        return self.get(row[0]) if row else None

    def failures(self) -> tuple[dict, ...]:
        rows = self.ledger._db.execute("""
            SELECT attempt_id, failure_type, failure_message FROM preparations
            WHERE failure_type IS NOT NULL ORDER BY sequence DESC LIMIT 5
        """).fetchall()
        return tuple({"attempt_id": r[0], "category": r[1], "message": r[2][:600]} for r in reversed(rows))

    def begin(self, parent_experiment: str, parent_commit: str) -> Preparation:
        with self.ledger._transaction():
            reasons = continuation_reasons(self.ledger._snapshot())
            if reasons:
                raise LedgerError("Cannot begin preparation: " + ", ".join(reasons))
            latest = self.latest()
            if latest is not None and latest.stage in ACTIVE_STAGES:
                raise LedgerError("An unfinished preparation must be reconciled first")
            policy = self.policy()
            if policy is not None and self.count() >= policy["max_attempts"]:
                raise LedgerError("PREPARATION_BUDGET_EXHAUSTED")
            if self.ledger.best().experiment_id != parent_experiment:
                raise LedgerError("Prepare from the current best experiment")
            if self.ledger.get(parent_experiment).git_commit != parent_commit:
                raise LedgerError("Preparation parent commit does not match the ledger")
            attempt_id = uuid.uuid4().hex
            self.ledger._db.execute("""
                INSERT INTO preparations (attempt_id, stage, parent_experiment, parent_commit, checkpoint_json, updated_at)
                VALUES (?, 'PREPARING', ?, ?, '{}', ?)
            """, (attempt_id, parent_experiment, parent_commit, _now()))
        return self.get(attempt_id)

    def update(self, attempt_id: str, stage: str, *, checkpoint: dict | None = None,
               failure_type: str | None = None, failure_message: str | None = None) -> Preparation:
        transitions = {
            "PREPARING": {"EDITING", "FAILED"}, "EDITING": {"COMMITTING", "FAILED"},
            "COMMITTING": {"COMMITTED"}, "COMMITTED": set(),
            "FAILED": {"RECOVERING", "RECOVERED"}, "RECOVERING": {"RECOVERED"},
            "RECOVERED": set(), "RESERVED": set(),
        }
        with self.ledger._transaction():
            current = self.get(attempt_id)
            if stage not in transitions[current.stage]:
                raise LedgerError(f"Invalid preparation transition: {current.stage} → {stage}")
            self.ledger._db.execute("""
                UPDATE preparations SET stage = ?, checkpoint_json = ?, failure_type = ?, failure_message = ?, updated_at = ?
                WHERE attempt_id = ?
            """, (stage, encode(current.checkpoint if checkpoint is None else checkpoint),
                  failure_type or current.failure_type, failure_message or current.failure_message, _now(), attempt_id))
        return self.get(attempt_id)

    def note_loop(self, state: str, message: str = "") -> None:
        with self.ledger._transaction():
            self.ledger._db.execute("INSERT OR REPLACE INTO metadata VALUES ('loop_status', ?)",
                                    (encode({"state": state, "message": message, "updated_at": _now()}),))

    def loop_status(self) -> dict:
        row = self.ledger._db.execute("SELECT value FROM metadata WHERE key = 'loop_status'").fetchone()
        return json.loads(row[0]) if row else {"state": "READY", "message": ""}
