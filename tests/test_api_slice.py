"""Local API integration: real Git/ledger, scripted research, no cloud resources."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError as exc:
    raise unittest.SkipTest("Install optional web and web-test dependencies for API tests") from exc

from research_intern.api.app import create_app, stream_status
from research_intern.controller.mission import MissionControl
from research_intern.ledger.sqlite import Ledger
from research_intern.offline import compose_loop
from test_execution_slice import WorkspaceTest

HEADERS = {"X-Research-Intern": "1"}


class ApiTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.mission = MissionControl(self.root, poll_interval=0.001)
        self.client = TestClient(create_app(self.root, mission=self.mission), base_url="http://127.0.0.1")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def create(self, **body):
        response = self.client.post("/api/runs", json=body, headers=HEADERS)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def wait_idle(self, run_id):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = self.client.get(f"/api/runs/{run_id}").json()
            if not status["driver"]["active"]:
                self.assertIsNone(status["driver"]["error"])
                return status
            time.sleep(0.1)
        self.fail("Offline driver did not finish")

    def test_assets_health_and_local_browser_boundary(self):
        for path, content in (("/", "Research mission control"), ("/assets/app.js", "EventSource"),
                              ("/assets/app.css", "@media")):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(content, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/api/health").json()["mode"], "simulated")
        self.assertEqual(self.client.get("/api/health", headers={"Host": "attacker.example"}).status_code, 400)
        self.assertEqual(self.client.post("/api/runs", json={}).status_code, 403)
        for extra in ({"Origin": "https://attacker.example"}, {"Sec-Fetch-Site": "cross-site"}):
            response = self.client.post("/api/runs", json={}, headers={**HEADERS, **extra})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get("/api/runs").json()["runs"], [])
        self.assertNotEqual(self.client.get("/assets/ledger.sqlite3").status_code, 200)

    def test_creation_validates_fixed_bounds_and_rejects_live_options(self):
        for body in ({"max_experiments": True}, {"max_experiments": -1}, {"max_experiments": 51},
                     {"max_attempts": 101}, {"scenario": "azure"}, {"repository": "/tmp"},
                     {"backend": "live"}, {"max_experiments": "4"}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/api/runs", json=body, headers=HEADERS).status_code, 422)
        self.assertEqual(self.client.get("/api/runs").json()["runs"], [])
        run = self.create(max_experiments=0)
        self.assertEqual(run["research_state"]["budget"]["remaining_experiments"], 0)
        self.assertEqual(run["experiments"][0]["experiment_id"], "EXP-000")
        self.assertFalse(run["driver"]["active"])

    def test_complete_research_journey_from_browser_commands(self):
        run_id = self.create()["id"]
        response = self.client.post(f"/api/runs/{run_id}/resume", json={}, headers=HEADERS)
        self.assertEqual(response.status_code, 202)
        status = self.wait_idle(run_id)
        self.assertEqual([r["decision"] for r in status["experiments"]],
                         ["KEEP", "KEEP", "REJECT", "FAILED", "GOAL_REACHED"])
        self.assertEqual(status["research_state"]["best_experiment"]["experiment_id"], "EXP-004")
        self.assertEqual(status["controller"]["state"], "STOPPED")
        self.assertEqual(status["copilot_usage"], 0)
        self.assertEqual(status["azure_gpu_hours"], 0)
        detail = self.client.get(f"/api/runs/{run_id}/experiments/EXP-002").json()
        self.assertEqual(detail["parent_experiment"], "EXP-001")
        self.assertEqual(detail["decision"], "REJECT")
        self.assertTrue(detail["diff"])
        self.assertEqual(len(detail["git_commit"]), 40)
        self.assertIn("hypothesis", detail["candidate"]["plan"])
        self.assertTrue(detail["candidate"]["validation"])
        self.assertTrue(detail["evaluation"]["constraint_results"])
        events = status["events"]
        self.assertEqual([e["sequence"] for e in events], sorted(e["sequence"] for e in events))
        self.assertIn("SUBMITTED", [e["state"] for e in events])
        listed = self.client.get("/api/runs").json()["runs"]
        self.assertEqual(listed[0]["id"], run_id)

    def test_recovery_scenario_exposes_failed_preparation(self):
        run_id = self.create(scenario="protected-first", max_experiments=1, max_attempts=2)["id"]
        self.client.post(f"/api/runs/{run_id}/resume", json={}, headers=HEADERS)
        status = self.wait_idle(run_id)
        self.assertEqual(status["preparation_budget"]["used"], 2)
        self.assertEqual(len(status["experiments"]), 2)
        self.assertTrue(status["preparation_failures"])
        self.assertEqual(status["experiments"][1]["decision"], "KEEP")

    def test_stopping_prepared_candidate_is_permanent_and_never_submits(self):
        run_id = self.create(max_experiments=1)["id"]
        root = self.mission.directory(run_id)
        with Ledger.reopen(root) as ledger:
            asyncio.run(compose_loop(ledger).run(steps=1))
        for _ in range(2):
            response = self.client.post(f"/api/runs/{run_id}/stop", json={}, headers=HEADERS)
            self.assertIn("HUMAN_STOP", response.json()["research_state"]["blocking_reasons"])
        self.client.post(f"/api/runs/{run_id}/resume", json={}, headers=HEADERS)
        status = self.wait_idle(run_id)
        self.assertEqual(status["experiments"][-1]["state"], "PREPARED")
        detail = self.client.get(f"/api/runs/{run_id}/experiments/EXP-001").json()
        self.assertIsNone(detail["job_id"])
        self.assertEqual(status["controller"]["state"], "STOPPED")

    def test_submitted_job_can_be_collected_after_human_stop(self):
        run_id = self.create(max_experiments=3)["id"]
        with Ledger.reopen(self.mission.directory(run_id)) as ledger:
            asyncio.run(compose_loop(ledger).run(steps=2))
            job_id = ledger.get("EXP-001").job_id
        self.client.post(f"/api/runs/{run_id}/stop", json={}, headers=HEADERS)
        self.client.post(f"/api/runs/{run_id}/resume", json={}, headers=HEADERS)
        status = self.wait_idle(run_id)
        self.assertEqual(len(status["experiments"]), 2)
        detail = self.client.get(f"/api/runs/{run_id}/experiments/EXP-001").json()
        self.assertEqual(detail["job_id"], job_id)
        self.assertEqual(detail["state"], "RECORDED")

    def test_worker_is_background_and_only_one_can_run(self):
        first = self.create()["id"]
        second = self.create()["id"]
        started = threading.Event()
        release = threading.Event()

        class WaitingLoop:
            async def run(self, **kwargs):
                started.set()
                while not release.is_set():
                    await asyncio.sleep(0.01)

        with patch("research_intern.controller.mission.compose_loop", return_value=WaitingLoop()):
            try:
                response = self.client.post(f"/api/runs/{first}/resume", json={}, headers=HEADERS)
                self.assertEqual(response.status_code, 202)
                self.assertTrue(started.wait(2))
                self.assertEqual(self.client.get("/api/health").json()["active_run_id"], first)
                self.assertEqual(self.client.post(f"/api/runs/{second}/resume", json={}, headers=HEADERS).status_code, 409)
                self.assertEqual(self.client.post("/api/runs", json={}, headers=HEADERS).status_code, 409)
                self.assertEqual(self.client.post(f"/api/runs/{first}/stop", json={}, headers=HEADERS).status_code, 200)
            finally:
                release.set()
                self.wait_idle(first)

    def test_restart_discovers_and_resumes_existing_pending_job(self):
        run_id = self.create(max_experiments=1)["id"]
        root = self.mission.directory(run_id)
        with Ledger.reopen(root) as ledger:
            asyncio.run(compose_loop(ledger).run(steps=2))
            original_job = ledger.get("EXP-001").job_id
        fresh = MissionControl(self.root, poll_interval=0.001)
        self.addCleanup(fresh.close)
        self.assertFalse(fresh.runs()["runs"][0]["driver"]["active"])
        fresh.start(run_id)
        deadline = time.monotonic() + 60
        while fresh.activity()["active_run_id"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNone(fresh.activity()["active_run_id"])
        self.assertEqual(fresh.experiment(run_id, "EXP-001")["job_id"], original_job)
        self.assertEqual(len(fresh.status(run_id)["experiments"]), 2)

    def test_shutdown_marks_interrupted_without_human_stop(self):
        run_id = self.create(max_experiments=1)["id"]
        self.mission.poll_interval = 5
        self.mission.start(run_id)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = self.mission.status(run_id)
            if status["experiments"][-1]["state"] == "PREPARED":
                break
            time.sleep(0.02)
        else:
            self.fail("No prepared candidate")
        self.mission.close()
        status = self.mission.status(run_id)
        self.assertFalse(status["driver"]["active"])
        self.assertEqual(status["controller"]["state"], "INTERRUPTED")
        self.assertNotIn("HUMAN_STOP", status["research_state"]["blocking_reasons"])

    def test_project_slot_only_inspects_contract_and_preserves_source(self):
        project = self.client.get("/api/project").json()
        self.assertEqual(project["status"], "missing")
        self.assertFalse(project["execution_available"])
        run_id = self.create()["id"]
        target = self.root / project["relative_path"]
        source = self.mission.directory(run_id) / "fixture_repo"
        shutil.copytree(source, target)
        original = (target / ".research_intern/contract.yaml").read_bytes()
        result = self.client.get("/api/project").json()
        self.assertEqual(result["status"], "contract_valid")
        self.assertFalse(result["execution_available"])
        self.assertEqual((target / ".research_intern/contract.yaml").read_bytes(), original)
        (target / ".research_intern/contract.yaml").write_text("unknown: true\n")
        self.assertEqual(self.client.get("/api/project").json()["status"], "needs_attention")

    def test_paths_missing_runs_and_unknown_experiments(self):
        for path in ("/api/runs/loop-absent", "/api/runs/..%5C..%5Cprivate", "/api/runs/loop-absent/events"):
            self.assertEqual(self.client.get(path).status_code, 404)
        run_id = self.create()["id"]
        self.assertEqual(self.client.get(f"/api/runs/{run_id}/experiments/EXP-999").status_code, 404)
        link = self.mission.directory(run_id).parent / "loop-unsafe"
        link.symlink_to(self.mission.directory(run_id), target_is_directory=True)
        self.assertEqual(self.client.get("/api/runs/loop-unsafe").status_code, 409)

    def test_stream_initial_snapshot_contains_persisted_events(self):
        run = self.create(max_experiments=0)

        class Connected:
            async def is_disconnected(self):
                return False

        async def read_once():
            generator = stream_status(Connected(), self.mission, run["id"], run)
            try:
                return await anext(generator)
            finally:
                await generator.aclose()

        message = asyncio.run(read_once())
        self.assertTrue(message.startswith("event: snapshot\ndata: "))
        data = json.loads(message.split("data: ", 1)[1])
        self.assertEqual(data["events"][0]["experiment_id"], "EXP-000")
        self.assertEqual(data["research_state"]["ledger_revision"], run["research_state"]["ledger_revision"])
