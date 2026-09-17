"""Prepare an uploaded source copy without executing it or adopting supplied Git state."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

from research_intern.contracts.loader import load_contract
from research_intern.contracts.models import CONTRACT_PATH
from research_intern.execution.outputs import read_json
from research_intern.validation.preflight import validate_files
from research_intern.workspace.git import GitWorkspace, WorkspaceError, snapshot_tree
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes


def contract_digest(repository: Path) -> str:
    return hashlib.sha256(child_path(repository, CONTRACT_PATH).read_bytes()).hexdigest()


def prepared_workspace(root: Path, repository: Path) -> dict | None:
    """Recheck the persisted import anchor; unknown work is retained and blocks setup."""
    receipt = child_path(root, "workspace.json")
    if not receipt.exists():
        if child_path(repository, ".git").exists():
            raise WorkspaceError("Unregistered Git repository: upload a source-only copy; existing history will not be changed")
        return None
    saved = read_json(receipt)
    if saved.get("version") != 1 or saved.get("repository") != "repository":
        raise WorkspaceError("Invalid source preparation record; inspect workspace.json")
    workspace = GitWorkspace(root, repository)
    files = workspace.verify_clean(saved["source_commit"])
    if ({name: asdict(entry) for name, entry in files.items()} != saved["files"]
            or contract_digest(repository) != saved["contract_sha256"]):
        raise WorkspaceError("Prepared source changed; preserve and reconcile it before continuing")
    return saved


def prepare_source(root: Path, repository: Path, expected_contract: str) -> dict:
    """Caller holds the project lock. Stage Git separately; never reset the source."""
    contract = load_contract(repository)
    if contract_digest(repository) != expected_contract:
        raise WorkspaceError("The contract changed. Reload the project before preparing its workspace")
    saved = prepared_workspace(root, repository)
    if saved is not None:
        return saved
    files = snapshot_tree(repository, omit_git=True)
    if any(name == contract.outputs.root.rstrip("/") for name in files):
        raise WorkspaceError("Keep measured outputs outside the candidate source; baseline evidence is imported separately")
    # Known credential files should never become source-history records. This is
    # a filename guard, not a claim to detect arbitrary secrets in source text.
    for name in files:
        filename = Path(name).name.casefold()
        if (filename == ".env" or (filename.startswith(".env.") and filename not in (".env.example", ".env.sample", ".env.template"))
                or filename in ("id_rsa", "id_ed25519") or filename.endswith((".pem", ".p12", ".pfx", ".key"))):
            raise WorkspaceError(f"Keep credential files outside candidate source: {name}")
    paths = tuple(name for name, entry in files.items() if entry.kind == "file")
    checks = validate_files(repository, paths)
    with tempfile.TemporaryDirectory(dir=root, prefix="prepare-") as temporary:
        staging_root = Path(temporary)
        staging = staging_root / "repository"
        staging.mkdir()
        for name, entry in files.items():
            destination = child_path(staging, name)
            if entry.kind == "directory":
                destination.mkdir()
            else:
                shutil.copyfile(child_path(repository, name), destination)
            destination.chmod(entry.permissions)
        git = GitWorkspace.initialize(staging_root, staging)
        if (git.verify_clean() != files or snapshot_tree(repository, omit_git=True) != files
                or contract_digest(repository) != expected_contract):
            raise WorkspaceError("Source changed during preparation; reload and retry without discarding edits")
        saved = {"version": 1, "repository": "repository", "source_commit": git.head,
                 "contract_sha256": expected_contract, "contract": contract.to_dict(),
                 "files": {name: asdict(entry) for name, entry in files.items()}, "checks": list(checks)}
        # Journal before publishing Git. An interrupted publication is explicit,
        # never interpreted as permission to replace unknown source/Git history.
        receipt = child_path(root, "workspace.json")
        if receipt.exists() or child_path(repository, ".git").exists():
            raise WorkspaceError("Workspace setup state changed during preparation")
        atomic_bytes(receipt, (json.dumps(saved, indent=2) + "\n").encode())
        git.git_directory.rename(repository / ".git")
    return prepared_workspace(root, repository)
