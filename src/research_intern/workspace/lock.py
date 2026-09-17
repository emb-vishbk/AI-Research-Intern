"""One process owns a run's mutable work; OS locks release on process exit."""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from research_intern.workspace.git import WorkspaceError
from research_intern.workspace.paths import child_path


class LockUnavailable(WorkspaceError):
    """Another process currently owns the workspace lock."""


class RunLock:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        self.path = child_path(self.root, "operation.lock")
        self.stream = None

    def __enter__(self) -> RunLock:
        self.stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.stream.write(b"0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK) or (os.name == "nt" and exc.errno == errno.EACCES):
                raise LockUnavailable("Another controller owns this run; status and stop remain available") from exc
            raise WorkspaceError(f"Unable to acquire the workspace lock: {exc}") from exc
        return self

    def __exit__(self, *args: object) -> None:
        if self.stream is not None:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None
        # Keep the inode: unlinking an advisory lock permits competing owners.


@contextmanager
def own_run(root: Path, existing: RunLock | None = None) -> Iterator[RunLock]:
    if existing is not None:
        if existing.root != root.resolve(strict=True) or existing.stream is None:
            raise WorkspaceError("A valid lock for this run is required")
        yield existing
    else:
        with RunLock(root) as lock:
            yield lock
