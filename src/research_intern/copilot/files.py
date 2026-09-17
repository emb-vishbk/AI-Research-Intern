"""Narrow source capabilities; no shell, Git, discovery, or execution authority."""

from __future__ import annotations

import hashlib
from pathlib import Path

from research_intern.contracts.models import ExperimentContract, relative_path
from research_intern.domain.experiments import SliceError
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes

MAX_TEXT_BYTES = 128 * 1024


class SourceAccessError(SliceError):
    """A model-supplied source operation is not authorized."""


class ControlledFiles:
    """Explicit read allowlist; existing-file replacement within contract scope.

    The caller supplies reviewed source paths, never data, weights, credentials,
    runtime state, or output artifacts. This is not an OS sandbox. The candidate
    controller must still verify the entire filesystem and Git metadata afterward.
    """

    def __init__(self, repository: Path, contract: ExperimentContract,
                 readable_paths: tuple[str, ...]):
        self.repository = repository.resolve(strict=True)
        self.contract = contract
        self.paths = tuple(sorted(set(relative_path(path) for path in readable_paths)))
        if not self.paths or len(self.paths) > 500:
            raise SourceAccessError("Choose 1–500 reviewed source files")
        for name in self.paths:
            self.read(name)

    def _path(self, name: str) -> Path:
        if not isinstance(name, str) or name not in self.paths:
            raise SourceAccessError("Path is not in the reviewed source allowlist")
        path = child_path(self.repository, name)
        if not path.is_file() or path.stat().st_nlink != 1:
            raise SourceAccessError("Source must be an existing regular, unlinked file")
        return path

    def read(self, name: str) -> dict[str, str]:
        path = self._path(name)
        with path.open("rb") as stream:
            raw = stream.read(MAX_TEXT_BYTES + 1)
        if len(raw) > MAX_TEXT_BYTES:
            raise SourceAccessError("Source exceeds the 128 KiB text limit")
        try:
            text = raw.decode("utf-8")
        except UnicodeError as exc:
            raise SourceAccessError("Only UTF-8 source text may be read") from exc
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "text": text}

    def write(self, name: str, expected_sha256: str, text: str) -> dict[str, str]:
        path = self._path(name)
        if not self.contract.allows(name):
            raise SourceAccessError("Protected or out-of-scope source cannot be written")
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise SourceAccessError("Replacement must be UTF-8 text of at most 128 KiB")
        if self.read(name)["sha256"] != expected_sha256:
            raise SourceAccessError("Source changed; read the current version before writing")
        atomic_bytes(path, text.encode("utf-8"), permissions=path.stat().st_mode & 0o777)
        result = self.read(name)
        return {"path": name, "sha256": result["sha256"]}