"""Shared local Copilot login state and narrowly inherited runtime environment."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

from research_intern.workspace.paths import child_path

TOKEN_VARIABLES = {"COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"}
_browser_states: dict[Path, tempfile.TemporaryDirectory] = {}


def prefer_browser_credentials() -> None:
    """Ignore token overrides in this process without changing the parent shell."""
    for key in list(os.environ):
        if key.upper() in TOKEN_VARIABLES:
            os.environ.pop(key)


def state_directory(workspace: Path) -> Path:
    session = _browser_states.get(workspace.resolve())
    if session is not None:
        return Path(session.name)
    path = child_path(workspace, ".runtime", "copilot-state")
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_browser_storage(workspace: Path) -> bool:
    """Use an owner-only tmpfs home on Linux, without requiring a desktop keyring.

    The CLI may write its fallback credential only into this volatile directory.
    Windows/macOS retain the provider's native credential manager instead.
    Returns whether session-only fallback storage is permitted.
    """
    if not sys.platform.startswith("linux"):
        return False
    root = Path("/dev/shm").resolve()
    mounts = Path("/proc/mounts").read_text().splitlines()
    if not any(len(parts := line.split()) > 2 and parts[1] == str(root)
               and parts[2] == "tmpfs" for line in mounts):
        raise RuntimeError("Private session storage is unavailable on this Linux host.")
    key = workspace.resolve()
    if key not in _browser_states:
        _browser_states[key] = tempfile.TemporaryDirectory(prefix="research-intern-auth-", dir=root)
        Path(_browser_states[key].name).chmod(0o700)
    return True


def close_browser_storage(workspace: Path) -> None:
    session = _browser_states.pop(workspace.resolve(), None)
    if session is not None:
        session.cleanup()


def uses_browser_storage(workspace: Path, configured_home: str | None) -> bool:
    """Accept fallback writes only to this process's verified private tmpfs home."""
    session = _browser_states.get(workspace.resolve())
    if session is None or not configured_home:
        return False
    path = Path(session.name)
    try:
        return (Path(configured_home).resolve() == path and path.is_dir()
                and path.stat().st_mode & 0o777 == 0o700
                and path.stat().st_uid == os.getuid())
    except (OSError, ValueError):
        return False


def runtime_environment(*, browser_login: bool = False) -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "APPDATA",
               "LOCALAPPDATA", "HOME", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
               "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS",
               "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME"}
    if not browser_login:
        allowed |= TOKEN_VARIABLES
    else:
        allowed |= {"BROWSER", "DISPLAY", "WAYLAND_DISPLAY", "WSL_DISTRO_NAME", "WSL_INTEROP"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}
