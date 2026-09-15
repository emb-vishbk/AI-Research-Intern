"""Opt-in web dependencies; localhost socket smoke test, no remote services."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import unittest

try:
    import httpx
    import uvicorn
except ImportError as exc:
    raise unittest.SkipTest("Install optional web and web-test dependencies for server tests") from exc

from test_execution_slice import WorkspaceTest


class ServerTests(WorkspaceTest):
    def test_real_http_assets_and_event_stream(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        log_path = self.root / "server.log"
        with log_path.open("w") as log:
            process = subprocess.Popen([
                sys.executable, "-B", "-c",
                "import sys; from pathlib import Path; import uvicorn; "
                "from research_intern.api.app import create_app; "
                "uvicorn.run(create_app(Path(sys.argv[1])), host='127.0.0.1', port=int(sys.argv[2]), log_level='warning')",
                str(self.root), str(port),
            ], stdout=log, stderr=log)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30) as client:
                    deadline = time.monotonic() + 20
                    while True:
                        try:
                            response = client.get("/api/health")
                            response.raise_for_status()
                            break
                        except httpx.TransportError:
                            if process.poll() is not None or time.monotonic() > deadline:
                                self.fail(log_path.read_text() or "Local server did not start")
                            time.sleep(0.1)
                    self.assertEqual(client.get("/").status_code, 200)
                    script = client.get("/assets/app.js")
                    self.assertIn("javascript", script.headers["content-type"])
                    self.assertIn("EventSource", script.text)
                    headers = {"X-Research-Intern": "1"}
                    response = client.post("/api/runs", json={"max_experiments": 0}, headers=headers)
                    self.assertEqual(response.status_code, 201)
                    run_id = response.json()["id"]
                    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
                        self.assertIn("text/event-stream", response.headers["content-type"])
                        lines = response.iter_lines()
                        self.assertEqual(next(lines), "event: snapshot")
                        data = json.loads(next(lines).removeprefix("data: "))
                        self.assertEqual(data["experiments"][0]["experiment_id"], "EXP-000")
                    self.assertEqual(client.post(f"/api/runs/{run_id}/resume", json={}, headers=headers).status_code, 202)
                    deadline = time.monotonic() + 20
                    while client.get(f"/api/runs/{run_id}").json()["driver"]["active"]:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.1)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    self.fail("Server did not shut down cleanly")
            self.assertNotIn("Traceback", log_path.read_text())
