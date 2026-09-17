"""Opt-in web dependencies; localhost socket smoke test, no remote services."""

from __future__ import annotations

import io
import json
import os
import signal
import socket
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

try:
    import httpx
    import uvicorn
except ImportError as exc:
    raise unittest.SkipTest("Install optional web and web-test dependencies for server tests") from exc

from test_execution_slice import WorkspaceTest
from research_intern.api.server import serve_workspace
from research_intern.workspace.lock import LockUnavailable, RunLock


class ServerStartupTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.server_root = self.root / ".runtime" / "web"
        self.server_root.mkdir(parents=True)
        self.metadata = self.server_root / "server.json"

    def test_duplicate_launch_preserves_owner_and_reports_its_port(self):
        original = json.dumps({"pid": os.getpid(), "port": 8765})
        self.metadata.write_text(original)
        errors = io.StringIO()
        with RunLock(self.server_root) as owner:
            inode = owner.path.stat().st_ino
            with patch("research_intern.api.server.uvicorn.run") as run, redirect_stderr(errors):
                self.assertEqual(serve_workspace(self.root, 8000), 1)
            run.assert_not_called()
            self.assertEqual(self.metadata.read_text(), original)
            self.assertEqual(owner.path.stat().st_ino, inode)
            with self.assertRaises(LockUnavailable):
                with RunLock(self.server_root):
                    self.fail("Duplicate launch released the original owner's lock")
        self.assertIn("already running", errors.getvalue())
        self.assertIn("http://127.0.0.1:8765", errors.getvalue())
        self.assertIn("Ctrl+C", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_duplicate_without_valid_metadata_still_reports_cleanly(self):
        for raw in (None, "not json", '{"port": true}', '{"port": 65536}'):
            with self.subTest(raw=raw):
                if raw is not None:
                    self.metadata.write_text(raw)
                errors = io.StringIO()
                with RunLock(self.server_root), redirect_stderr(errors):
                    self.assertEqual(serve_workspace(self.root, 8000), 1)
                self.assertIn("already running", errors.getvalue())
                self.assertNotIn("http://", errors.getvalue())

    def test_stale_metadata_does_not_block_start_and_lock_covers_serving(self):
        self.metadata.write_text('{"pid": 1, "port": 8765}')

        def run(app, **config):
            self.assertEqual(config["host"], "127.0.0.1")
            self.assertEqual(config["port"], 8000)
            self.assertEqual(config["workers"], 1)
            self.assertEqual(json.loads(self.metadata.read_text()), {"pid": os.getpid(), "port": 8000})
            with self.assertRaises(LockUnavailable):
                with RunLock(self.server_root):
                    self.fail("Server must retain the lock while serving")

        with patch("research_intern.api.server.uvicorn.run", side_effect=run), redirect_stdout(io.StringIO()):
            self.assertEqual(serve_workspace(self.root, 8000), 0)
        self.assertFalse(self.metadata.exists())
        with RunLock(self.server_root) as lock:
            self.assertTrue(lock.path.exists())

    def test_failed_server_start_releases_lock_and_metadata(self):
        with patch("research_intern.api.server.uvicorn.run", side_effect=SystemExit(3)), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                serve_workspace(self.root, 8000)
        self.assertFalse(self.metadata.exists())
        with RunLock(self.server_root):
            pass


class ServerTests(WorkspaceTest):
    def test_real_http_assets_and_event_stream(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        log_path = self.root / "server.log"
        with log_path.open("w") as log:
            command = [
                sys.executable, "-B", "-c",
                "import sys; from pathlib import Path; "
                "from research_intern.api.server import serve_workspace; "
                "sys.exit(serve_workspace(Path(sys.argv[1]), int(sys.argv[2])))",
                str(self.root), str(port),
            ]
            process = subprocess.Popen(command, stdout=log, stderr=log)
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
                    duplicate = subprocess.run(command[:-1] + [str(port - 1)], capture_output=True,
                                               text=True, timeout=20)
                    self.assertEqual(duplicate.returncode, 1, duplicate.stderr)
                    self.assertIn("already running", duplicate.stderr)
                    self.assertIn(f"http://127.0.0.1:{port}", duplicate.stderr)
                    self.assertNotIn("Traceback", duplicate.stderr)
                    self.assertIsNone(process.poll())
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
                if os.name == "nt":
                    process.terminate()
                else:
                    # Match Ctrl+C in the launcher's terminal. Uvicorn re-raises
                    # SIGTERM after shutdown, which may leave diagnostic metadata.
                    process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    self.fail("Server did not shut down cleanly")
            self.assertNotIn("Traceback", log_path.read_text())
            server_root = self.root / ".runtime" / "web"
            if os.name != "nt":
                self.assertFalse((server_root / "server.json").exists())
            with RunLock(server_root):
                pass
