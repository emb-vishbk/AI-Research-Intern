"""Browser onboarding exercises real routes and child processes with fake providers."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    from fastapi.testclient import TestClient
except ImportError as exc:
    raise unittest.SkipTest("Install web and web-test extras for browser API tests") from exc

from research_intern.api.app import create_app
from research_intern.connections import BrowserProviders, Connections, run_login_process, safe_authorization_url
from research_intern.controller.mission import MissionControl
from research_intern.copilot.auth import close_browser_storage, prepare_browser_storage, state_directory
from research_intern.execution import identity
from research_intern.signin import SignInError

HEADERS = {"X-Research-Intern": "1"}


class FakeProviders:
    def __init__(self):
        self.calls = []
        self.waiting = False
        self.failure = None
        self.closed = False
        self.entered = threading.Event()

    def connect(self, provider, update, cancelled, host=None):
        self.calls.append(provider)
        self.entered.set()
        update(status="waiting", authorization_url="https://microsoft.com/devicelogin" if provider == "azure" else "https://github.com/login/device", user_code="ABCD-1234")
        if self.waiting:
            cancelled.wait(10)
        if self.failure:
            raise self.failure
        if not cancelled.is_set():
            update(status="connected", message="Connection checked.", models=["model-a", "model-b"] if provider == "copilot" else [],
                   authorization_url=None, user_code=None, storage="session")

    def azure(self, *args):
        return self.connect("azure", *args)

    def copilot(self, *args):
        return self.connect("copilot", *args)

    def bind(self, settings):
        pass

    def close(self):
        self.closed = True


class ConnectionApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.mission = MissionControl(self.root)
        self.fake = FakeProviders()
        self.connections = Connections(self.root, self.mission, providers=self.fake)
        self.client = TestClient(create_app(self.root, mission=self.mission, connections=self.connections), base_url="http://127.0.0.1")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def post(self, path, body=None):
        return self.client.post(path, json=body or {}, headers=HEADERS)

    def wait_idle(self, provider):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.client.get("/api/connections").json()[provider]
            if state["status"] not in ("connecting", "waiting", "checking"):
                # State is published before releasing the project operation.
                self.connections._thread.join(2)
                return state
            time.sleep(0.01)
        self.fail("Provider worker did not finish")

    def test_buttons_and_initial_reads_do_not_start_authentication(self):
        page = self.client.get("/").text
        for text in ("Sign in to Azure", "Sign in to Copilot", "Prepare research"):
            self.assertIn(text, page)
        self.assertNotIn("Use saved service settings", page)
        state = self.client.get("/api/workspace").json()
        self.assertEqual(state["connections"]["azure"]["status"], "signed_out")
        self.assertEqual(self.fake.calls, [])
        self.assertIsNone(state["run"])

    def test_independent_sign_ins_model_selection_and_no_research(self):
        self.assertEqual(self.post("/api/connections/azure/signin").status_code, 202)
        self.assertEqual(self.wait_idle("azure")["status"], "connected")
        self.assertEqual(self.client.get("/api/connections").json()["copilot"]["status"], "signed_out")
        draft = self.root / ".runtime/live-settings.draft.json"
        settings = {"azure": {"subscription_id": "reviewed"}, "copilot": {"model": ""}, "scoring": {"keep": True}}
        draft.write_text(json.dumps(settings))
        self.assertEqual(self.post("/api/connections/copilot/signin").status_code, 202)
        self.assertEqual(self.wait_idle("copilot")["models"], ["model-a", "model-b"])
        self.assertEqual(self.post("/api/connections/copilot/model", {"model": "unavailable"}).status_code, 409)
        self.assertEqual(self.post("/api/connections/copilot/model", {"model": "model-b"}).status_code, 200)
        settings["copilot"]["model"] = "model-b"
        self.assertEqual(json.loads(draft.read_text()), settings)
        self.assertFalse((self.root / ".runtime/research-project/live.json").exists())
        self.assertEqual(list(self.root.rglob("ledger.sqlite3")), [])

    def test_local_origin_boundary_and_provider_input_validation(self):
        path = "/api/connections/copilot/signin"
        self.assertEqual(self.client.post(path, json={}).status_code, 403)
        self.assertEqual(self.client.post(path, json={}, headers={**HEADERS, "Origin": "https://attacker.example"}).status_code, 403)
        for body in ({"host": "evil.example"}, {"host": "github.com --allow-all"}, {"host": "https://github.com"}):
            self.assertEqual(self.post(path, body).status_code, 409)
        self.assertEqual(self.post(path, {"token": "never-accepted"}).status_code, 422)
        self.assertEqual(self.post("/api/connections/unknown/signin").status_code, 422)
        self.assertFalse(self.fake.calls)

    def test_model_selection_needs_no_service_settings_file(self):
        self.assertEqual(self.post("/api/connections/copilot/signin").status_code, 202)
        self.wait_idle("copilot")
        response = self.post("/api/connections/copilot/model", {"model": "model-a"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["copilot"]["selected_model"], "model-a")
        self.assertFalse((self.root / ".runtime/live-settings.draft.json").exists())

    def test_waiting_cancel_retry_and_research_mutual_exclusion(self):
        self.fake.waiting = True
        self.post("/api/connections/copilot/signin")
        self.assertTrue(self.fake.entered.wait(2))
        self.assertEqual(self.post("/api/connections/azure/signin").status_code, 409)
        self.assertEqual(self.post("/api/runs").status_code, 409)
        state = self.client.get("/api/connections").json()["copilot"]
        self.assertEqual(state["authorization_url"], "https://github.com/login/device")
        self.assertEqual(self.post("/api/connections/copilot/cancel").status_code, 200)
        state = self.wait_idle("copilot")
        self.assertEqual(state["status"], "cancelled")
        self.assertIsNone(state["authorization_url"])
        self.fake.waiting = False
        self.assertEqual(self.post("/api/connections/azure/signin").status_code, 202)
        self.assertEqual(self.wait_idle("azure")["status"], "connected")

    def test_denied_access_is_distinct_from_login_and_other_errors_are_redacted(self):
        self.fake.failure = SignInError("GitHub sign-in succeeded; Copilot policy denies access.", "access_denied")
        self.post("/api/connections/copilot/signin")
        self.assertEqual(self.wait_idle("copilot")["status"], "denied")
        self.fake.failure = RuntimeError("secret-token-do-not-expose")
        self.post("/api/connections/azure/signin")
        state = self.wait_idle("azure")
        self.assertEqual(state["status"], "error")
        self.assertNotIn("secret-token", json.dumps(state))

    def test_existing_live_model_is_immutable(self):
        self.post("/api/connections/copilot/signin")
        self.wait_idle("copilot")
        live = self.root / ".runtime/research-project/live.json"
        live.parent.mkdir()
        live.write_text(json.dumps({"settings": {"copilot": {"model": "model-a"}}}))
        before = live.read_bytes()
        self.assertEqual(self.post("/api/connections/copilot/model", {"model": "model-b"}).status_code, 409)
        self.assertEqual(live.read_bytes(), before)

    def test_shutdown_cancels_pending_authorization(self):
        self.fake.waiting = True
        self.post("/api/connections/azure/signin")
        self.assertTrue(self.fake.entered.wait(2))
        self.connections.close()
        self.assertFalse(self.connections._thread.is_alive())
        self.assertTrue(self.fake.closed)
        self.assertEqual(self.connections.snapshot()["azure"]["status"], "cancelled")

    def test_server_restart_expires_old_service_check_without_losing_evidence(self):
        proof = self.root / ".runtime/research-project/services-verified.json"
        proof.parent.mkdir(parents=True)
        proof.write_text(json.dumps({"copilot_authenticated": True, "azure": {"compute": "reviewed"}}))
        other_mission = MissionControl(self.root)
        other_connections = Connections(self.root, other_mission, providers=FakeProviders())
        # The fixture's existing server owns a web lock; simulate a clean restart.
        self.client.__exit__(None, None, None)
        with TestClient(create_app(self.root, mission=other_mission, connections=other_connections), base_url="http://127.0.0.1") as client:
            self.assertEqual(client.get("/api/connections").json()["azure"]["status"], "signed_out")
        saved = json.loads(proof.read_text())
        self.assertTrue(saved["invalidated"])
        self.assertEqual(saved["azure"], {"compute": "reviewed"})


class ProviderBoundaryTests(unittest.TestCase):
    def test_url_allowlist_rejects_credentials_lookalikes_and_local_callbacks(self):
        for url in ("https://github.com.evil.example/login/device", "https://github.com@evil.example/login/device", "javascript:alert(1)", "http://github.com/login/device", "https://github.com:444/login/device", "http://127.0.0.1/callback?code=private", "https://github.com/other"):
            self.assertFalse(safe_authorization_url("copilot", url), url)
        self.assertTrue(safe_authorization_url("copilot", "https://github.com/login/oauth/authorize?state=abc"))
        self.assertTrue(safe_authorization_url("azure", "https://microsoft.com/devicelogin"))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux tmpfs storage")
    def test_session_credentials_use_private_ram_and_clean_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            try:
                self.assertTrue(prepare_browser_storage(workspace))
                state = state_directory(workspace)
                self.assertTrue(state.is_relative_to(Path("/dev/shm").resolve()))
                self.assertEqual(state.stat().st_mode & 0o777, 0o700)
                (state / "config.json").write_text("fake session credential")
                self.assertFalse(list(workspace.rglob("config.json")))
            finally:
                close_browser_storage(workspace)
            self.assertFalse(state.exists())

    def test_child_output_is_filtered_and_partial_links_are_not_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            updates = []
            script = "import sys, time; print('secret-token-should-stay-private'); sys.stdout.write('https://github.com/login/oauth/authorize?state='); sys.stdout.flush(); time.sleep(.05); print('complete'); print('https://evil.example/login/device')"
            run_login_process([sys.executable, "-c", script], env=dict(os.environ), cwd=temporary,
                              update=lambda **v: updates.append(v), cancelled=threading.Event())
            self.assertEqual(updates[-1]["authorization_url"], "https://github.com/login/oauth/authorize?state=complete")
            self.assertNotIn("secret-token", json.dumps(updates))
            self.assertNotIn("evil.example", json.dumps(updates))

    def test_child_cancellation_and_timeout_are_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            cancel = threading.Event()
            timer = threading.Timer(.2, cancel.set)
            timer.start()
            try:
                started = time.monotonic()
                with self.assertRaisesRegex(SignInError, "cancelled"):
                    run_login_process([sys.executable, "-c", "import time; time.sleep(20)"], env=dict(os.environ), cwd=temporary, update=lambda **_: None, cancelled=cancel)
                self.assertLess(time.monotonic() - started, 5)
                with self.assertRaisesRegex(SignInError, "expired"):
                    run_login_process([sys.executable, "-c", "import time; time.sleep(20)"], env=dict(os.environ), cwd=temporary, update=lambda **_: None, cancelled=threading.Event(), timeout=.1)
            finally:
                timer.cancel()

    def test_plaintext_fallback_is_refused_outside_session_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = "import sys; print('Store token in plaintext config file?', flush=True); sys.stdin.readline()"
            with self.assertRaisesRegex(SignInError, "No unencrypted login was saved"):
                run_login_process([sys.executable, "-c", script], env=dict(os.environ), cwd=temporary, update=lambda **_: None, cancelled=threading.Event())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux session sign-in terminal")
    def test_terminal_storage_prompt_completes_only_in_verified_session_home(self):
        # Reproduce a helper which refuses storage on piped stdin, without
        # contacting GitHub or using a real token. No newline ends the prompt.
        script = (
            "import os, sys, termios; from pathlib import Path; "
            "interactive = sys.stdin.isatty() and sys.stdout.isatty(); "
            "print('Login succeeded, but the token was not saved.' if not interactive else 'Authorizing', flush=True); "
            "sys.exit(1) if not interactive else None; "
            "assert not termios.tcgetattr(0)[3] & termios.ECHO; "
            "print('https://github.com/login/device', flush=True); "
            "print('Store token in plaintext config file? (y/N) ', end='', flush=True); "
            "assert sys.stdin.readline().strip() == 'y'; "
            "Path(os.environ['COPILOT_HOME'], 'config.json').write_text('fake-session-secret'); "
            "print('Signed in successfully. fake-session-secret', flush=True)"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.addCleanup(close_browser_storage, root)
            prepare_browser_storage(root)
            home = state_directory(root)
            environment = dict(os.environ, COPILOT_HOME=str(home))
            with self.assertRaises(SignInError) as piped:
                run_login_process([sys.executable, "-c", script], env=environment, cwd=root,
                                  update=lambda **_: None, cancelled=threading.Event(), timeout=5)
            self.assertEqual(piped.exception.code, "storage_unavailable")
            self.assertFalse((home / "config.json").exists())
            updates = []
            run_login_process([sys.executable, "-c", script], env=environment, cwd=root,
                              update=lambda **v: updates.append(v), cancelled=threading.Event(),
                              memory_fallback=True, timeout=5)
            self.assertEqual((home / "config.json").read_text(), "fake-session-secret")
            self.assertFalse((root / "config.json").exists())
            self.assertEqual(updates[-1]["authorization_url"], "https://github.com/login/device")
            self.assertNotIn("fake-session-secret", json.dumps(updates))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux session sign-in terminal")
    def test_unverified_fallback_directory_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("research_intern.connections.subprocess.Popen") as spawn:
                with self.assertRaisesRegex(SignInError, "could not be verified"):
                    run_login_process(["unused"], env={"COPILOT_HOME": temporary}, cwd=temporary,
                                      update=lambda **_: None, cancelled=threading.Event(), memory_fallback=True)
                spawn.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux session sign-in terminal")
    def test_terminal_children_are_reaped_on_cancellation_and_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.addCleanup(close_browser_storage, root)
            prepare_browser_storage(root)
            environment = dict(os.environ, COPILOT_HOME=str(state_directory(root)))
            script = "import os, time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid())); time.sleep(30)"
            for cancel_after, timeout, message in ((.5, 5, "cancelled"), (None, .5, "expired")):
                with self.subTest(message=message):
                    cancellation = threading.Event()
                    timer = threading.Timer(cancel_after, cancellation.set) if cancel_after else None
                    if timer:
                        timer.start()
                    try:
                        with self.assertRaisesRegex(SignInError, message):
                            run_login_process([sys.executable, "-c", script], env=environment, cwd=root,
                                              update=lambda **_: None, cancelled=cancellation,
                                              memory_fallback=True, timeout=timeout)
                    finally:
                        if timer:
                            timer.cancel()
                    pid = int((root / "child.pid").read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)

    def test_failed_login_reports_safe_cause_instead_of_blaming_browser_approval(self):
        cases = (
            ("Login succeeded, but the token was not saved.", "storage_unavailable"),
            ("unable to get local issuer certificate", "certificate_trust"),
            ("TypeError: fetch failed", "provider_network"),
            ("OAuth access_denied", "authorization_denied"),
            ("unrecognized private provider failure", "helper_exit"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for message, expected_code in cases:
                with self.subTest(expected_code=expected_code):
                    updates = []
                    script = "import sys; print(" + repr(message + " private-account-and-token") + "); sys.exit(7)"
                    with self.assertRaises(SignInError) as raised:
                        run_login_process([sys.executable, "-c", script], env=dict(os.environ), cwd=temporary,
                                          update=lambda **v: updates.append(v), cancelled=threading.Event())
                    self.assertEqual(raised.exception.code, expected_code)
                    self.assertNotIn("private-account-and-token", str(raised.exception))
                    self.assertEqual(updates, [])

    def test_copilot_provider_uses_session_home_and_never_starts_research(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".runtime").mkdir()
            (root / "runtime").touch()
            (root / ".runtime/live-settings.draft.json").write_text(json.dumps({"copilot": {"runtime_path": "runtime", "model": ""}}))
            states = []
            with patch("research_intern.copilot.install.install"), patch("research_intern.connections.prepare_browser_storage", return_value=True), \
                 patch("research_intern.connections.copilot_login_command", return_value=(["fake-cli", "login"], {})), \
                 patch("research_intern.connections.run_login_process") as login, \
                 patch("research_intern.connections.copilot_models", AsyncMock(return_value=["model-a"])):
                BrowserProviders(root).copilot(lambda **v: states.append(v), threading.Event())
            self.assertTrue(login.call_args.kwargs["memory_fallback"])
            self.assertEqual(states[-1]["status"], "connected")
            self.assertEqual(states[-1]["models"], ["model-a"])
            self.assertFalse(list(root.rglob("ledger.sqlite3")))

    def test_reconnecting_an_account_invalidates_service_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            proof = root / ".runtime/research-project/services-verified.json"
            proof.parent.mkdir(parents=True)
            proof.write_text(json.dumps({"checked_at": "2026-09-23T00:00:00+00:00", "copilot_authenticated": True}))
            mission = MissionControl(root)
            connections = Connections(root, mission, providers=FakeProviders())
            try:
                connections.start("azure")
                connections._thread.join(3)
                self.assertTrue(json.loads(proof.read_text())["invalidated"])
                self.assertEqual(list(root.rglob("ledger.sqlite3")), [])
            finally:
                connections.close()

    @unittest.skipUnless(importlib.util.find_spec("msal"), "Install the Azure extra")
    def test_azure_credential_cache_and_runtime_handoff_never_serialize_tokens(self):
        application = MagicMock()
        application.initiate_device_flow.return_value = {"verification_uri": "https://microsoft.com/devicelogin", "user_code": "ABCD1234", "device_code": "private-code", "expires_at": time.time() + 900}
        application.acquire_token_by_device_flow.return_value = {"access_token": "private-access-token"}
        application.get_accounts.return_value = [{"username": "researcher@example.com"}]
        application.acquire_token_silent.return_value = {"access_token": "private-access-token", "expires_in": 600}
        updates = []
        with patch("msal.PublicClientApplication", return_value=application):
            credential = identity.AzureBrowserCredential()
            self.assertEqual(credential.sign_in(lambda **v: updates.append(v), threading.Event()), "researcher@example.com")
            self.assertEqual(credential.get_token(identity.ARM_SCOPE).token, "private-access-token")
        self.assertNotIn("private-", json.dumps(updates))
        identity.remember_credential("test-subscription", credential)
        try:
            self.assertIs(identity.credential_for("test-subscription"), credential)
            credential.close()
            self.assertIs(identity.credential_for("test-subscription"), credential)
        finally:
            identity.forget_credential("test-subscription")


if __name__ == "__main__":
    unittest.main()
