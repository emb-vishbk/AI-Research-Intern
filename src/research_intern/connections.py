"""User-initiated browser connections for the single local research workspace.

Only allowlisted provider links and safe status fields reach the dashboard.
Authentication never creates a coding session, submits a job, or grants budgets.
"""
from __future__ import annotations

import asyncio
import copy
import errno
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from research_intern.copilot.auth import (
    close_browser_storage, prepare_browser_storage, runtime_environment, state_directory, uses_browser_storage,
)
from research_intern.domain.experiments import SliceError
from research_intern.execution.identity import AzureBrowserCredential, AzureSignInError, forget_credential, remember_credential
from research_intern.signin import SignInError, copilot_login_command, copilot_models, service_settings
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes

PROVIDERS = ("azure", "copilot")
BUSY = {"connecting", "waiting", "checking"}


def login_process_error(output: str, exit_code: int) -> SignInError:
    """Classify private CLI output without publishing account data or credentials."""
    lowered = output.lower()
    if any(marker in lowered for marker in (
        "token was not saved", "failed to save", "failed to store", "could not store",
        "keychain unavailable", "credential store is unavailable", "cannot prompt",
        "stdin is not a tty", "not a terminal",
    )):
        return SignInError(
            "Copilot could not save the login for this app session. GitHub browser approval "
            "may have succeeded, but local credential storage did not complete. "
            "Diagnostic: credential_storage.", "storage_unavailable")
    if any(marker in lowered for marker in (
        "unable to get local issuer", "unable_to_get_issuer", "unable_to_verify_leaf",
        "self signed certificate", "self-signed certificate", "certificate verify failed",
        "cert_has_expired",
    )):
        return SignInError(
            "Copilot could not verify the network certificate while completing sign-in. "
            "The application's Copilot helper needs the organization's trusted CA certificate. "
            "Diagnostic: certificate_trust.", "certificate_trust")
    if any(marker in lowered for marker in ("fetch failed", "econnrefused", "etimedout", "enotfound", "eai_again")):
        return SignInError(
            "Copilot could not reach GitHub while completing sign-in. Check the application's "
            "proxy and network access. Diagnostic: provider_network.", "provider_network")
    if any(marker in lowered for marker in ("access_denied", "authorization denied", "authorization was denied")):
        return SignInError(
            "GitHub authorization was denied. Retry and review the browser's authorization message. "
            "Diagnostic: authorization_denied.", "authorization_denied")
    return SignInError(
        f"The Copilot sign-in helper exited with code {exit_code} without confirming a usable login. "
        "This does not confirm a Copilot licence or organization-policy problem. "
        f"Diagnostic: helper_exit_{exit_code}.", "helper_exit")


def safe_authorization_url(provider: str, value: str, host: str = "github.com") -> bool:
    try:
        url = urlsplit(value)
        if url.scheme != "https" or url.username or url.password or url.port not in (None, 443):
            return False
        if provider == "copilot":
            return url.hostname == host and url.path in ("/login/device", "/login/oauth/authorize")
        return url.hostname in ("login.microsoftonline.com", "microsoft.com", "www.microsoft.com", "login.microsoft.com") and (
            url.path in ("/devicelogin", "/device", "/common/oauth2/deviceauth")
            or url.path.endswith("/oauth2/v2.0/authorize"))
    except ValueError:
        return False


def run_login_process(command, *, env, cwd, update, cancelled, provider="copilot", host="github.com",
                      memory_fallback=False, timeout=900):
    """Drain bounded output, handle provider prompts, and reap on cancel/timeout.

    Raw output stays private. No shell is involved, including on Windows.
    The fallback 'yes' is possible only for the verified Linux tmpfs home.
    Copilot skips interactive credential prompts on piped stdin, so that path
    uses a private pseudo-terminal. Its input is never echoed or shown in the UI.
    """
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    terminal = None
    if memory_fallback:
        if not sys.platform.startswith("linux") or not uses_browser_storage(Path(cwd), env.get("COPILOT_HOME")):
            raise SignInError("Private session credential storage could not be verified.", "storage_unavailable")
        import pty
        import select
        import termios

        terminal, child_terminal = pty.openpty()
        try:
            attributes = termios.tcgetattr(child_terminal)
            attributes[3] &= ~(termios.ECHO | termios.ECHONL)
            termios.tcsetattr(child_terminal, termios.TCSANOW, attributes)
            termios.tcsetwinsize(child_terminal, (24, 4096))
            process = subprocess.Popen(command, cwd=cwd, env=dict(env, TERM="dumb", NO_COLOR="1"),
                                       stdin=child_terminal, stdout=child_terminal, stderr=child_terminal, **options)
        except BaseException:
            os.close(terminal)
            raise
        finally:
            os.close(child_terminal)
    else:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **options)
    output = queue.Queue(maxsize=32)
    stopping = threading.Event()

    def read():
        try:
            while not stopping.is_set():
                if terminal is None:
                    chunk = process.stdout.read1(4096)
                else:
                    if not select.select([terminal], [], [], 0.2)[0]:
                        if process.poll() is not None:
                            break
                        continue
                    try:
                        chunk = os.read(terminal, 4096)
                    except OSError as exc:
                        # Linux signals terminal EOF with EIO when the child exits.
                        if exc.errno == errno.EIO:
                            break
                        raise
                if not chunk:
                    break
                while not stopping.is_set():
                    try:
                        output.put(chunk, timeout=0.1)
                        break
                    except queue.Full:
                        continue
        finally:
            while not stopping.is_set():
                try:
                    output.put(None, timeout=0.1)
                    break
                except queue.Full:
                    continue

    reader = threading.Thread(target=read, name="provider-output", daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    text = ""
    answered = False
    try:
        while True:
            if cancelled.is_set():
                raise SignInError("Sign-in cancelled.", "cancelled")
            if time.monotonic() >= deadline:
                raise SignInError("Sign-in expired. Try again.", "expired")
            try:
                chunk = output.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if chunk is None:
                break
            text = (text + chunk.decode("utf-8", errors="replace"))[-32768:]
            clean = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
            for url in re.findall(r"https://[^\s<>\"']+(?=[\s<>\"'])", clean):
                if safe_authorization_url(provider, url, host):
                    update(status="waiting", message="Continue in your browser to authorize this connection.",
                           authorization_url=url)
            code = re.search(r"(?:one-time code|enter (?:the )?code)[: ]+([A-Z0-9-]{4,20})\b", clean, re.I)
            if code:
                update(user_code=code.group(1))
            if "Store token in plaintext config file?" in clean and not answered:
                if terminal is not None:
                    os.write(terminal, b"y\n")
                else:
                    process.stdin.write(b"n\n")
                    process.stdin.flush()
                answered = True
                if not memory_fallback:
                    raise SignInError("Your operating system credential manager is unavailable. No unencrypted login was saved.", "storage_unavailable")
        code = process.wait(timeout=5)
        if code:
            raise login_process_error(text, code)
    finally:
        stopping.set()
        if process.poll() is None:
            if os.name == "nt":
                process.terminate()
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
        reader.join(timeout=2)
        if terminal is not None:
            os.close(terminal)
        else:
            process.stdin.close()
            process.stdout.close()


class BrowserProviders:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.azure_credential = None
        self.azure_subscription = None

    def settings(self):
        try:
            return service_settings(self.workspace)
        except SignInError:
            return None, None, False

    def azure(self, update, cancelled, host=None):
        credential = AzureBrowserCredential()
        account = credential.sign_in(update, cancelled)
        if cancelled.is_set():
            return
        self.azure_credential = credential
        settings, _, _ = self.settings()
        if settings:
            self.bind(settings)
        update(status="connected", message="Microsoft sign-in complete. Workspace permissions are checked before research starts.",
               account=account, storage="session", authorization_url=None, user_code=None)

    def bind(self, settings):
        if self.azure_credential is not None:
            subscription = settings["azure"]["subscription_id"]
            if self.azure_subscription and self.azure_subscription != subscription:
                forget_credential(self.azure_subscription)
            remember_credential(subscription, self.azure_credential)
            self.azure_subscription = subscription

    def copilot(self, update, cancelled, host=None):
        from research_intern.copilot.install import install
        from copilot._cli_version import get_runtime_platform
        update(message="Preparing the verified Copilot sign-in helper. The first connection may take a few minutes.")
        install(self.workspace, cancelled=cancelled)
        if cancelled.is_set():
            return
        memory = prepare_browser_storage(self.workspace)
        settings, draft_path, immutable = self.settings()
        runtime = settings["copilot"]["runtime_path"] if settings else ""
        if not runtime or not child_path(self.workspace, runtime).is_file():
            if immutable:
                raise SignInError("The runtime fixed for this research run is missing. Restore it before reconnecting.")
            directory = child_path(self.workspace, ".runtime/copilot-sdk")
            directory.mkdir(parents=True, exist_ok=True)
            env = dict(runtime_environment(), COPILOT_CLI_EXTRACT_DIR=str(directory))
            run_login_process([sys.executable, "-m", "copilot", "download-runtime"], env=env,
                              cwd=self.workspace, update=lambda **_: None, cancelled=cancelled, timeout=300)
            filename = "copilot-runtime.exe" if os.name == "nt" else "copilot-runtime"
            path = directory / "prebuilds" / get_runtime_platform() / filename
            if not path.is_file():
                raise SignInError("Copilot runtime setup did not complete. Retry the connection.")
            runtime = path.relative_to(self.workspace).as_posix()
            if settings:
                settings["copilot"]["runtime_path"] = runtime
                atomic_bytes(draft_path, json.dumps(settings, indent=2).encode())
        update(storage="session" if memory else "system", message="Opening Copilot sign-in…")
        # An explicit reconnect must permit a different account even when the
        # previous account signed in successfully but was denied model access.
        command, environment = copilot_login_command(self.workspace, host=host)
        if memory:
            environment["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/research-intern-session"
        if os.name != "nt":
            environment["BROWSER"] = "/bin/true"
        run_login_process(command, env=environment, cwd=self.workspace, update=update,
                          cancelled=cancelled, host=host or "github.com", memory_fallback=memory)
        update(status="checking", message="Signed in to GitHub. Checking Copilot access…", authorization_url=None, user_code=None)
        if cancelled.is_set():
            return
        models = asyncio.run(copilot_models(self.workspace, runtime))
        if not models:
            raise SignInError("The account returned no Copilot models. Check its Copilot access.", "access_denied")
        selected = settings["copilot"]["model"] if settings else ""
        if immutable and selected not in models:
            raise SignInError("This account cannot use the model fixed for the live run.", "access_denied")
        if selected not in models:
            selected = ""
        update(status="connected", message="Copilot connected. Choose a coding model below." if not selected else "Copilot connected.",
               models=models, selected_model=selected, model_locked=immutable, runtime_path=runtime,
               authorization_url=None, user_code=None)

    def close(self):
        if self.azure_subscription:
            forget_credential(self.azure_subscription)
        self.azure_credential = None
        close_browser_storage(self.workspace)


class Connections:
    def __init__(self, workspace, mission, *, providers=None):
        self.workspace, self.mission = workspace, mission
        self.providers = providers or BrowserProviders(workspace)
        self._guard = threading.Lock()
        self._thread = None
        self._cancelled = threading.Event()
        self._closed = False
        self._state = {name: {"status": "signed_out", "message": "Sign in to connect.",
                              "authorization_url": None, "user_code": None, "models": [],
                              "selected_model": "", "model_locked": False, "storage": None}
                       for name in PROVIDERS}
        self._state["copilot"]["host"] = os.environ.get("RESEARCH_INTERN_COPILOT_HOST", "")

    def snapshot(self):
        with self._guard:
            return copy.deepcopy(self._state)

    def project_replaced(self):
        """Keep account sign-ins, but release the former run's model selection lock."""
        with self._guard:
            self._state['copilot']['model_locked'] = False

    def start(self, provider, *, host=None):
        if provider not in PROVIDERS:
            raise SliceError("Unknown sign-in provider")
        if host and (provider != "copilot" or not re.fullmatch(r"(?:github\.com|[a-z0-9][a-z0-9.-]*\.ghe\.com)", host)):
            raise SliceError("Use github.com or your organization's hostname ending in .ghe.com.")
        with self._guard:
            if self._closed or self._thread and self._thread.is_alive():
                raise SliceError("Wait for the current connection attempt or cancel it.")
            operation = self.mission.project_operation()
            operation.__enter__()
            try:
                self.mission.live.invalidate_service_check()
            except BaseException:
                operation.__exit__(None, None, None)
                raise
            self._cancelled = threading.Event()
            self._state[provider].update(status="connecting", message="Preparing sign-in…", authorization_url=None,
                                         user_code=None, models=[], selected_model="")
            self._thread = threading.Thread(target=self._run, args=(provider, host, operation),
                                            name=f"connect-{provider}", daemon=True)
            try:
                self._thread.start()
            except BaseException:
                operation.__exit__(None, None, None)
                raise
        return self.snapshot()

    def _run(self, provider, host, operation):
        def update(**fields):
            if fields.get("authorization_url") and not safe_authorization_url(provider, fields["authorization_url"], host or "github.com"):
                raise SignInError("The provider returned an unexpected sign-in address.")
            with self._guard:
                if not self._cancelled.is_set():
                    self._state[provider].update(fields)
        try:
            getattr(self.providers, provider)(update, self._cancelled, host)
        except SignInError as exc:
            update(status="denied" if exc.code == "access_denied" else "error", message=str(exc))
        except AzureSignInError as exc:
            update(status="error", message=str(exc))
        except Exception:
            update(status="error", message="Connection could not be completed. Check network access and organization sign-in requirements, then retry.")
        finally:
            with self._guard:
                self._state[provider].update(authorization_url=None, user_code=None)
                if self._cancelled.is_set():
                    self._state[provider].update(status="cancelled", message="Sign-in cancelled. You can try again.")
            operation.__exit__(None, None, None)

    def cancel(self, provider):
        with self._guard:
            if self._state[provider]["status"] in BUSY:
                self._cancelled.set()
                self._state[provider].update(message="Cancelling sign-in…", authorization_url=None, user_code=None)
        return self.snapshot()

    def select_model(self, model):
        with self.mission.project_operation():
            current = self.snapshot()["copilot"]
            if current["status"] != "connected" or model not in current["models"]:
                raise SliceError("Choose an available model after connecting Copilot.")
            runtime = Path(self.workspace) / ".runtime"
            if (runtime / "research-project/live.json").exists() or (runtime / "live-settings.draft.json").exists():
                try:
                    settings, path, immutable = service_settings(self.workspace)
                except SignInError as exc:
                    raise SliceError(str(exc)) from None
            else:
                settings, path, immutable = None, None, False
            if immutable:
                raise SliceError("The model is fixed for this research run.")
            if settings:
                settings["copilot"]["model"] = model
                atomic_bytes(path, json.dumps(settings, indent=2).encode())
            with self._guard:
                self._state["copilot"].update(selected_model=model, message="Copilot connected. Coding model saved.")
        return self.snapshot()

    def close(self):
        with self._guard:
            self._closed = True
            self._cancelled.set()
            thread = self._thread
        if thread:
            thread.join(timeout=35)
            if thread.is_alive():
                raise SliceError("A provider connection is still shutting down.")
        self.providers.close()
