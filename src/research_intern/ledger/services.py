"""Durable service intent and conservative admission; never a billing meter.

Uses the run's SQLite database, independently of scientific result tables.
Copilot reservations can be settled with provider usage or proof no prompt was
sent. Unknown outcomes retain their reservation. Human credit additions are
append-only; the original contract and service allowance remain unchanged.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from research_intern.domain.experiments import SliceError
from research_intern.workspace.paths import child_path


class ServiceAdmissionError(SliceError):
    pass


@dataclass(frozen=True)
class ServiceIntent:
    request_id: str
    name: str
    payload: dict
    units: int
    remote_id: str | None


class ServiceJournal:
    def __init__(self, root: Path, *, service: str, unit: str, max_units: int):
        if service not in ("azure", "copilot", "copilot_credits") or not unit or type(max_units) is not int or max_units < 0:
            raise ServiceAdmissionError("Declare a supported service and non-negative integer allowance")
        self.service, self.unit, self.max_units = service, unit, max_units
        self.path = child_path(root, "ledger.sqlite3")
        if self.path.exists() and (not self.path.is_file() or self.path.stat().st_nlink != 1):
            raise ServiceAdmissionError("Service state must be a regular unlinked database")
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS service_policy (
                    service TEXT PRIMARY KEY, unit TEXT NOT NULL,
                    max_units INTEGER NOT NULL, namespace TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS service_intents (
                    service TEXT NOT NULL, request_id TEXT NOT NULL, name TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL, units INTEGER NOT NULL, remote_id TEXT,
                    PRIMARY KEY(service, request_id));
                CREATE TABLE IF NOT EXISTS service_settlements (
                    service TEXT NOT NULL, request_id TEXT NOT NULL, units INTEGER NOT NULL,
                    evidence TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(service, request_id));
                CREATE TABLE IF NOT EXISTS credit_additions (
                    request_id TEXT PRIMARY KEY, units INTEGER NOT NULL, contract_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT OR IGNORE INTO service_policy VALUES (?, ?, ?, ?)",
                               (service, unit, max_units, uuid4().hex))
            row = connection.execute("SELECT unit, max_units, namespace FROM service_policy WHERE service=?",
                                     (service,)).fetchone()
            if row[:2] != (unit, max_units):
                raise ServiceAdmissionError("Saved service allowance cannot be changed on restart")
            self.namespace = row[2]

    def _connection(self):
        # sqlite3's context manager commits but does NOT close; own both lifetimes.
        from contextlib import contextmanager

        @contextmanager
        def opened():
            connection = sqlite3.connect(self.path, timeout=10)
            try:
                with connection:
                    yield connection
            finally:
                connection.close()
        return opened()

    def get(self, request_id: str) -> ServiceIntent | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT request_id, name, payload, units, remote_id FROM service_intents WHERE service=? AND request_id=?",
                (self.service, request_id)).fetchone()
        return None if row is None else ServiceIntent(row[0], row[1], json.loads(row[2]), row[3], row[4])

    def find_name(self, name: str) -> ServiceIntent | None:
        with self._connection() as connection:
            row = connection.execute("SELECT request_id FROM service_intents WHERE service=? AND name=?",
                                     (self.service, name)).fetchone()
        return None if row is None else self.get(row[0])

    def reserve(self, request_id: str, payload: dict, *, units: int) -> tuple[ServiceIntent, bool]:
        if not isinstance(request_id, str) or not request_id or len(request_id) > 100 or type(units) is not int or units <= 0:
            raise ServiceAdmissionError("A request needs an identifier and positive integer reservation")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > 1024 * 1024:
            raise ServiceAdmissionError("Service intent exceeds the 1 MiB limit")
        digest = hashlib.sha256((self.namespace + request_id).encode()).hexdigest()[:32]
        name = f"ri-{self.service}-{digest}"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload, units FROM service_intents WHERE service=? AND request_id=?",
                                     (self.service, request_id)).fetchone()
            if row is not None:
                if row != (encoded, units):
                    raise ServiceAdmissionError("An existing request cannot be rebound to different inputs")
                created = False
            else:
                used, _, _, _ = self._totals(connection)
                if used + units > self._limit(connection):
                    raise ServiceAdmissionError("Conservative service allowance exhausted")
                connection.execute("INSERT INTO service_intents VALUES (?, ?, ?, ?, ?, NULL)",
                                   (self.service, request_id, name, encoded, units))
                created = True
        intent = self.get(request_id)
        assert intent is not None
        return intent, created

    def attach(self, request_id: str, remote_id: str) -> None:
        if not isinstance(remote_id, str) or not remote_id or len(remote_id) > 2048:
            raise ServiceAdmissionError("Expected a bounded remote identifier")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT remote_id FROM service_intents WHERE service=? AND request_id=?",
                                     (self.service, request_id)).fetchone()
            if row is None or row[0] not in (None, remote_id):
                raise ServiceAdmissionError("A remote identifier cannot be replaced")
            connection.execute("UPDATE service_intents SET remote_id=? WHERE service=? AND request_id=?",
                               (remote_id, self.service, request_id))

    def _limit(self, connection) -> int:
        additions = (connection.execute("SELECT COALESCE(SUM(units), 0) FROM credit_additions").fetchone()[0]
                     if self.service == "copilot_credits" else 0)
        return self.max_units + additions

    def _totals(self, connection):
        return connection.execute("""
            SELECT COALESCE(SUM(COALESCE(s.units,i.units)),0),
                   COALESCE(SUM(s.units),0),
                   COALESCE(SUM(CASE WHEN s.request_id IS NULL THEN i.units ELSE 0 END),0),
                   COUNT(CASE WHEN s.request_id IS NULL THEN 1 END)
            FROM service_intents i LEFT JOIN service_settlements s
              ON s.service=i.service AND s.request_id=i.request_id WHERE i.service=?
        """, (self.service,)).fetchone()

    def settle(self, request_id: str, units: int, evidence: dict) -> None:
        """Record final usage once, preserving the original reservation and proof."""
        if self.service not in {"copilot", "copilot_credits"} or type(units) is not int or units < 0:
            raise ServiceAdmissionError("Only Copilot usage can be settled with non-negative units")
        if evidence.get("kind") not in {"provider_usage", "prompt_not_sent", "legacy_session_rejected"}:
            raise ServiceAdmissionError("A settlement requires usage or pre-prompt rejection evidence")
        encoded = json.dumps(evidence, sort_keys=True, allow_nan=False)
        if len(encoded) > 8192:
            raise ServiceAdmissionError("Settlement evidence is too large")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM service_intents WHERE service=? AND request_id=?",
                                      (self.service, request_id)).fetchone():
                raise ServiceAdmissionError("Cannot settle an unknown request")
            old = connection.execute("SELECT units,evidence FROM service_settlements WHERE service=? AND request_id=?",
                                     (self.service, request_id)).fetchone()
            if old is not None and old != (units, encoded):
                raise ServiceAdmissionError("Final usage evidence cannot be replaced")
            connection.execute("INSERT OR IGNORE INTO service_settlements(service,request_id,units,evidence) VALUES (?,?,?,?)",
                               (self.service, request_id, units, encoded))

    def add_credits(self, request_id: str, units: int, contract_sha256: str) -> None:
        if self.service != "copilot_credits" or type(units) is not int or not 0 < units <= 1_000_000_000_000:
            raise ServiceAdmissionError("Choose a positive additional credit allowance, at most 1,000,000 credits")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ServiceAdmissionError("A credit addition needs a bounded request ID")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute("SELECT units,contract_sha256 FROM credit_additions WHERE request_id=?", (request_id,)).fetchone()
            if old is not None and old != (units, contract_sha256):
                raise ServiceAdmissionError("This credit addition was already recorded with different values")
            connection.execute("INSERT OR IGNORE INTO credit_additions(request_id,units,contract_sha256) VALUES (?,?,?)",
                               (request_id, units, contract_sha256))

    def usage(self) -> dict:
        with self._connection() as connection:
            used, spent, held, unresolved = self._totals(connection)
            limit = self._limit(connection)
        return {"unit": self.unit, "reserved": used, "limit": limit, "initial_limit": self.max_units,
                "added": limit - self.max_units, "spent": spent, "held": held, "unresolved": unresolved,
                "remaining": limit - used, "actual_usage": spent if not unresolved and self.service == "copilot_credits" else None,
                "billing_cap": False}
