"""Explicit provider browser sign-in before starting the local dashboard.

Provider CLIs own OAuth/MFA and credential storage. No token is requested,
returned to the browser UI, copied to settings, or sent to a coding session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess

from copilot import CopilotClient, RuntimeConnection
from copilot._cli_version import CLI_VERSION, get_runtime_platform

from research_intern.copilot.auth import prefer_browser_credentials, runtime_environment, state_directory
from research_intern.copilot.spike import shutdown
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes


class SignInError(RuntimeError):
    def __init__(self, message: str, code: str = "sign_in_failed"):
        super().__init__(message)
        self.code = code


def service_settings(workspace: Path) -> tuple[dict, Path, bool]:
    live = child_path(workspace, ".runtime/research-project/live.json")
    if live.is_file():
        return json.loads(live.read_text())["settings"], live, True
    draft = child_path(workspace, ".runtime/live-settings.draft.json")
    if not draft.is_file():
        raise SignInError("Prepare the service settings draft before signing in.")
    return json.loads(draft.read_text()), draft, False


def azure_sign_in(subscription: str, *, device_code: bool = False) -> None:
    if not isinstance(subscription, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", subscription):
        raise SignInError("The service draft needs a valid Azure subscription ID.")
    cli = shutil.which("az")
    if not cli:
        raise SignInError("Install Azure CLI in the same operating system as this application.")
    probe = [cli, "account", "get-access-token", "--subscription", subscription, "--output", "none"]
    print("Checking your Azure sign-in...", flush=True)
    if subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120).returncode:
        print("Sign in to Microsoft in your browser and complete your organisation's MFA/SSO.", flush=True)
        command = [cli, "login", "--output", "none"]
        if device_code:
            command.append("--use-device-code")
        environment = dict(os.environ, AZURE_CORE_LOGIN_EXPERIENCE_V2="off")
        if subprocess.run(command, env=environment, timeout=900).returncode:
            raise SignInError("Azure sign-in was cancelled or unsuccessful. Run --sign-in again.")
        if subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120).returncode:
            raise SignInError("The signed-in Azure account cannot authenticate for the configured subscription.")
    # AzureCliCredential uses the CLI's selected account. Match the reviewed target.
    if subprocess.run([cli, "account", "set", "--subscription", subscription],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60).returncode:
        raise SignInError("Could not select the configured Azure subscription.")
    print("Azure sign-in verified.", flush=True)


def copilot_login_command(workspace: Path, *, device_code: bool = False, host: str | None = None) -> tuple[list[str], dict]:
    if host is not None and not re.fullmatch(r"(?:github\.com|[a-z0-9][a-z0-9.-]*\.ghe\.com)", host):
        raise SignInError("Use github.com or your organisation's GitHub Enterprise Cloud hostname ending in .ghe.com.")
    executable = "copilot.exe" if os.name == "nt" else "copilot"
    cli = child_path(workspace, ".runtime/copilot-cli", f"{CLI_VERSION}-{get_runtime_platform()}", "package", executable)
    if not cli.is_file():
        raise SignInError("Install the browser sign-in helper with the project Python: tools/install_copilot_cli.py")
    command = [str(cli), "login", "--device-code" if device_code else "--web-flow"]
    if host:
        command.extend(["--host", "https://" + host])
    environment = runtime_environment(browser_login=True)
    environment["COPILOT_HOME"] = str(state_directory(workspace))
    return command, environment


async def copilot_models(workspace: Path, runtime_path: str) -> list[str] | None:
    runtime = child_path(workspace, runtime_path)
    if not runtime.is_file():
        raise SignInError("Provision the configured Copilot SDK runtime first.")
    client = CopilotClient(connection=RuntimeConnection.for_stdio(path=str(runtime)), mode="empty",
                           working_directory=str(workspace), base_directory=str(state_directory(workspace)),
                           use_logged_in_user=True, env=runtime_environment())
    # This SDK logger otherwise prints raw RPC failure bodies and headers. The
    # serial connection check reports a bounded, classified message instead.
    rpc_logger = logging.getLogger("copilot._jsonrpc")
    previous_disabled = rpc_logger.disabled
    rpc_logger.disabled = True
    try:
        async with asyncio.timeout(45):
            await client.start()
            if not (await client.get_auth_status()).isAuthenticated:
                return None
            models = await client.list_models()
            return sorted({model.id for model in models})
    except Exception as exc:
        # Provider text is inspected locally, never returned to the UI or ledger.
        if "403" in str(exc) or "not authorized to use this Copilot feature" in str(exc):
            raise SignInError("GitHub sign-in succeeded, but Copilot access was denied. Check the account's Copilot license and your organization's Copilot CLI policy.", "access_denied") from None
        raise SignInError(f"Copilot account verification failed ({type(exc).__name__}). Check browser sign-in and proxy access.") from None
    finally:
        try:
            errors = await shutdown(client, None, abort=False)
        finally:
            rpc_logger.disabled = previous_disabled
        if errors:
            raise SignInError("Copilot sign-in check could not shut down cleanly. Retry before starting research.")


def sign_in(workspace: Path, *, device_code: bool = False, host: str | None = None) -> None:
    settings, path, immutable = service_settings(workspace)
    # Browser sign-in must not silently authenticate as a different environment token.
    # Remove overrides only from this launcher process and the server it starts.
    prefer_browser_credentials()
    command, environment = copilot_login_command(workspace, device_code=device_code, host=host)
    azure_sign_in(settings["azure"]["subscription_id"], device_code=device_code)
    models = None
    if host is None:
        try:
            models = asyncio.run(copilot_models(workspace, settings["copilot"]["runtime_path"]))
        except SignInError:
            # A stored credential can outlive its account/session validity. One
            # explicit provider login can repair it; never silently loop retries.
            print("Copilot's saved sign-in could not be verified. Sign in again in your browser.", flush=True)
    if models is None or host is not None:
        print("Sign in to GitHub Copilot in your browser. Complete organisation SSO if requested.", flush=True)
        print("If the browser does not open, use the URL shown below. For a device code, restart with --sign-in --device-code.", flush=True)
        # Inherit the user's terminal so provider keychain and MFA prompts work.
        # This login subcommand cannot start a model session or edit source.
        if subprocess.run(command, cwd=workspace, env=environment, timeout=900).returncode:
            raise SignInError("Copilot sign-in was cancelled or unsuccessful. Run --sign-in again.")
        models = asyncio.run(copilot_models(workspace, settings["copilot"]["runtime_path"]))
    if not models:
        raise SignInError("Copilot returned no available models. Check your account's Copilot access and organisation policy.")
    selected = settings["copilot"]["model"]
    if selected not in models:
        if immutable:
            raise SignInError("This account cannot access the model fixed for the live run; its settings were not changed.")
        print("Choose a coding model available to your account:")
        for index, model in enumerate(models, 1):
            print(f"  {index}. {model}")
        while True:
            choice = input("Model number: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(models):
                selected = models[int(choice) - 1]
                break
            print("Enter one of the listed numbers.")
        settings["copilot"]["model"] = selected
        atomic_bytes(path, json.dumps(settings, indent=2).encode())
    print(f"Copilot sign-in verified. Coding model: {selected}.", flush=True)
    print("Sign-in is complete. No training job or AI coding session was started.", flush=True)
