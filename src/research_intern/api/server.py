"""Start one localhost server, with an actionable duplicate-launch message."""

from __future__ import annotations

import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path

import uvicorn

from research_intern.api.app import create_app
from research_intern.domain.experiments import SliceError
from research_intern.execution.outputs import read_json
from research_intern.workspace.lock import LockUnavailable, RunLock
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes


def report_existing_server(server_root: Path) -> None:
    print("Research Intern is already running for this workspace.", file=sys.stderr)
    try:
        port = read_json(child_path(server_root, "server.json")).get("port")
    except (OSError, SliceError):
        port = None
    # Metadata is only a hint; the OS lock is the authority for ownership.
    if type(port) is int and 1 <= port <= 65535:
        print(f"Last recorded server address: http://127.0.0.1:{port}", file=sys.stderr)
    print("Use the existing server, or press Ctrl+C in its original terminal before restarting.", file=sys.stderr)


def serve_workspace(workspace: Path, port: int) -> int:
    server_root = child_path(workspace, ".runtime", "web")
    server_root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        try:
            lock = stack.enter_context(RunLock(server_root))
        except LockUnavailable:
            report_existing_server(server_root)
            return 1
        metadata_path = child_path(server_root, "server.json")
        atomic_bytes(metadata_path, json.dumps({"pid": os.getpid(), "port": port}).encode("utf-8"))
        try:
            print(f"Research workspace: http://127.0.0.1:{port}", flush=True)
            # Keep ownership through startup, serving and driver cleanup. Bound
            # SSE draining so open browser tabs cannot prevent normal shutdown.
            uvicorn.run(create_app(workspace, server_lock=lock), host="127.0.0.1", port=port,
                        workers=1, timeout_graceful_shutdown=3)
        finally:
            metadata_path.unlink(missing_ok=True)
    return 0
