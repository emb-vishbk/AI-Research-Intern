"""Durable service intent and conservative admission; never a billing meter.

Uses the run's SQLite database, independently of scientific result tables. Every
reserved unit remains charged, including ambiguous and failed requests. A timeout
or missing remote job is not permission to issue another request.
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
                used = connection.execute("SELECT COALESCE(SUM(units), 0) FROM service_intents WHERE service=?",
                                          (self.service,)).fetchone()[0]
                if used + units > self.max_units:
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

    def usage(self) -> dict:
        with self._connection() as connection:
            used = connection.execute("SELECT COALESCE(SUM(units), 0) FROM service_intents WHERE service=?",
                                      (self.service,)).fetchone()[0]
        return {"unit": self.unit, "reserved": used, "limit": self.max_units,
                "remaining": self.max_units - used, "actual_usage": None, "billing_cap": False}
