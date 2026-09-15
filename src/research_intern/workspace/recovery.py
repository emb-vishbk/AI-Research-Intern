"""Recover only code states whose contents were recorded by the controller."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from research_intern.contracts.models import ExperimentContract
from research_intern.domain.experiments import SliceError
from research_intern.workspace.git import Entry, GitWorkspace, PreparedParent, WorkspaceError, snapshot_tree
from research_intern.workspace.paths import child_path


def entries(data: dict) -> dict[str, Entry]:
    return {name: Entry(**value) for name, value in data.items()}


def parent_from_checkpoint(checkpoint: dict) -> PreparedParent:
    parent = checkpoint["parent"]
    return PreparedParent(parent["commit"], entries(parent["files"]), entries(parent["metadata"]))


def capture_failure(workspace: GitWorkspace, checkpoint: dict) -> dict:
    result = dict(checkpoint)
    try:
        result["after_files"] = {name: asdict(entry) for name, entry in snapshot_tree(workspace.repository, omit_git=True).items()}
        result["after_metadata"] = {name: asdict(entry) for name, entry in snapshot_tree(workspace.git_directory).items()}
    except (OSError, ValueError, SliceError) as exc:
        result["recovery_blocked"] = str(exc)
    return result


def atomic_bytes(path: Path, raw: bytes, *, staging: Path | None = None, permissions: int | None = None) -> None:
    """Stage on the same filesystem, outside the repository during restoration."""
    with tempfile.NamedTemporaryFile(dir=staging or path.parent, prefix="recovery-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if permissions is not None:
            temporary.chmod(permissions)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_recovery(workspace: GitWorkspace, checkpoint: dict, *, partial: bool = False) -> dict[str, Entry]:
    parent = parent_from_checkpoint(checkpoint)
    if snapshot_tree(workspace.git_directory) != parent.metadata:
        raise WorkspaceError("Git metadata changed; failed edits require manual reconciliation")
    current = snapshot_tree(workspace.repository, omit_git=True)
    if current == parent.files:
        return current
    if "after_files" not in checkpoint or "after_metadata" not in checkpoint:
        raise WorkspaceError("Interrupted edits have no recorded final snapshot; refusing to erase unknown work")
    if entries(checkpoint["after_metadata"]) != parent.metadata:
        raise WorkspaceError("The failed proposer changed Git metadata; automatic cleanup is blocked")
    after = entries(checkpoint["after_files"])
    if not partial and current != after:
        raise WorkspaceError("Workspace changed after the failed attempt; refusing to erase newer work")
    if partial:
        for name in current.keys() | after.keys() | parent.files.keys():
            original, failed, actual = parent.files.get(name), after.get(name), current.get(name)
            permitted = (original, failed)
            if original is not None and failed is not None and original.kind != failed.kind:
                permitted += (None,)
            if actual not in permitted:
                raise WorkspaceError("Workspace diverged from the recorded restoration; manual reconciliation is required")
    workspace._verify_repository()
    if workspace.head != parent.commit:
        raise WorkspaceError("The failed preparation's HEAD has changed")
    if workspace._tree(parent.commit) != {p: e.digest for p, e in parent.files.items() if e.kind == "file"}:
        raise WorkspaceError("The recovery parent does not match its committed code")
    return current


def archive_failed_edits(workspace: GitWorkspace, checkpoint: dict, attempt_directory: Path) -> None:
    current = verify_recovery(workspace, checkpoint)
    parent = parent_from_checkpoint(checkpoint)
    archive = child_path(attempt_directory, "rejected_files")
    archive.mkdir(exist_ok=True)
    for name, entry in current.items():
        if entry.kind == "file" and entry != parent.files.get(name):
            target = child_path(archive, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_bytes(target, child_path(workspace.repository, name).read_bytes())


def restore_failed_edits(workspace: GitWorkspace, checkpoint: dict, attempt_directory: Path) -> None:
    """No reset/clean: restore recorded paths from trusted parent blobs, idempotently."""
    current = verify_recovery(workspace, checkpoint, partial=True)
    parent = parent_from_checkpoint(checkpoint)
    if current == parent.files:
        return
    for name in sorted(current, key=lambda p: (p.count("/"), p), reverse=True):
        entry, original = current[name], parent.files.get(name)
        if original is None or original.kind != entry.kind:
            path = child_path(workspace.repository, name)
            path.rmdir() if entry.kind == "directory" else path.unlink()
    for name, entry in sorted(parent.files.items(), key=lambda pair: (pair[0].count("/"), pair[0])):
        path = child_path(workspace.repository, name)
        if entry.kind == "directory":
            path.mkdir(exist_ok=True)
        elif current.get(name) != entry:
            raw = workspace._git("cat-file", "blob", entry.digest)
            atomic_bytes(path, raw, staging=attempt_directory, permissions=entry.permissions)
        path.chmod(entry.permissions)
    if snapshot_tree(workspace.repository, omit_git=True) != parent.files:
        raise WorkspaceError("Restoration did not reproduce the recorded parent filesystem")
    workspace.verify_clean(parent.commit)


def finish_commit(workspace: GitWorkspace, checkpoint: dict, contract: ExperimentContract,
                  parent_experiment: str) -> tuple[str, str]:
    """Finish a journaled Git operation or adopt its already-created exact commit."""
    parent = parent_from_checkpoint(checkpoint)
    expected = entries(checkpoint["validated_files"])
    workspace._verify_repository()
    if snapshot_tree(workspace.repository, omit_git=True) != expected:
        raise WorkspaceError("Code changed after the commit checkpoint; refusing automatic reservation")
    if workspace.head == parent.commit:
        index = workspace.staged_files()
        allowed = ({p: e.digest for p, e in parent.files.items() if e.kind == "file"},
                   {p: e.digest for p, e in expected.items() if e.kind == "file"})
        if index not in allowed:
            raise WorkspaceError("Unexpected staging state requires manual reconciliation")
        # The index may already contain the validated edit. Recheck all policy and
        # filesystem changes before completing the controller-owned commit.
        parent = PreparedParent(parent.commit, parent.files, snapshot_tree(workspace.git_directory))
        return workspace.commit_candidate(parent, contract, expected, parent_experiment)
    commit = workspace.head
    if checkpoint.get("git_commit", commit) != commit:
        raise WorkspaceError("HEAD differs from the journaled candidate commit")
    if workspace._git("rev-parse", f"{commit}^").decode().strip() != parent.commit:
        raise WorkspaceError("Unrecorded HEAD is not the journaled candidate's child")
    workspace.verify_clean(commit)
    diff = workspace._git("diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames",
                          parent.commit, commit, "--").decode("utf-8")
    workspace._git("update-ref", f"refs/research-intern/candidates/{commit}", commit)
    return commit, diff
