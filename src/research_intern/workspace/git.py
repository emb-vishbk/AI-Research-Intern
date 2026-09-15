"""Controlled Git operations on a dedicated, application-owned offline repository.

These checks protect the candidate boundary; they are not an OS sandbox against
concurrent hostile processes. Live agents and arbitrary repository onboarding are
deliberately outside this slice.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from research_intern.contracts.models import ExperimentContract, relative_path
from research_intern.domain.experiments import SliceError
from research_intern.workspace.paths import child_path

PLATFORM_ROOT = Path(__file__).resolve().parents[3]
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TREE_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 10000


class WorkspaceError(SliceError):
    """The dedicated workspace cannot safely enter the requested Git transition."""


class PermissionViolation(WorkspaceError):
    """Actual changes crossed the immutable experiment boundary."""


@dataclass(frozen=True)
class Entry:
    kind: str
    digest: str
    permissions: int


def snapshot_tree(root: Path, *, omit_git: bool = False) -> dict[str, Entry]:
    """Include ignored/untracked files and empty directories; never follow links."""
    result = {}
    aliases = set()
    total = 0
    if root.is_symlink() or not root.is_dir():
        raise WorkspaceError("Expected a regular workspace directory")

    def walk(directory: Path) -> None:
        nonlocal total
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            if omit_git and name == ".git":
                continue
            if name.casefold() in aliases:
                raise WorkspaceError("Case-colliding workspace paths are unsupported")
            aliases.add(name.casefold())
            if omit_git:
                relative_path(name)
            info = path.lstat()
            permissions = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                result[name] = Entry("directory", "", permissions)
                walk(path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if info.st_size > MAX_FILE_BYTES:
                    raise WorkspaceError("Prepared fixture files must not exceed 16 MiB")
                total += info.st_size
                if total > MAX_TREE_BYTES:
                    raise WorkspaceError("Prepared fixture trees must not exceed 128 MiB")
                # Git blob hashes permit comparison with the committed tree without
                # executing filters or trusting index 'assume unchanged' flags.
                raw = path.read_bytes()
                digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
                result[name] = Entry("file", digest, permissions)
            else:
                raise WorkspaceError("Workspace trees cannot contain symlinks, hardlinks, or special files")
            if len(result) > MAX_ENTRIES:
                raise WorkspaceError("Prepared fixture trees must not exceed 10000 entries")

    try:
        walk(root)
    except OSError as exc:
        raise WorkspaceError("Cannot inspect the complete workspace tree") from exc
    return result


@dataclass(frozen=True)
class PreparedParent:
    commit: str
    files: dict[str, Entry]
    metadata: dict[str, Entry]


def verify_versionable_directories(files: dict[str, Entry]) -> None:
    parents = {parent.as_posix() for name, entry in files.items() if entry.kind == "file"
               for parent in Path(name).parents}
    if any(entry.kind == "directory" and name not in parents for name, entry in files.items()):
        raise WorkspaceError("Empty directories cannot be recorded in Git; remove or populate them before continuing")


class GitWorkspace:
    def __init__(self, run_root: Path, repository: Path):
        try:
            runtime = child_path(PLATFORM_ROOT, ".runtime")
            self.run_root = child_path(runtime, run_root.absolute().relative_to(runtime).as_posix())
            self.repository = child_path(self.run_root, repository.absolute().relative_to(self.run_root).as_posix())
        except (ValueError, SliceError) as exc:
            raise WorkspaceError("Use a dedicated experiment repository inside a run under .runtime/") from exc
        self.git_directory = child_path(self.repository, ".git")
        if not self.git_directory.is_dir():
            raise WorkspaceError("An independent .git directory is required; linked worktrees are unsupported")
        self.executable = shutil.which("git")
        if self.executable is None:
            raise WorkspaceError("The candidate workflow requires an existing Git executable")
        self.empty_hooks = child_path(self.run_root, "candidate_empty_hooks")
        self.empty_hooks.mkdir(exist_ok=True)
        self.environment = {key: value for key, value in os.environ.items()
                            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
        self.environment.update(
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
            GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1", GIT_NO_REPLACE_OBJECTS="1",
            GIT_ATTR_NOSYSTEM="1", TMPDIR=str(self.run_root), TMP=str(self.run_root), TEMP=str(self.run_root),
        )
        self._verify_repository()

    def _git(self, *arguments: str) -> bytes:
        try:
            return subprocess.run(
                [self.executable, "-c", "user.name=Offline Research Intern", "-c",
                 "user.email=simulation@example.invalid", "-c", "commit.gpgSign=false",
                 "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false",
                 "-c", f"core.hooksPath={self.empty_hooks}", "-c", "core.attributesFile=/dev/null",
                 "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "protocol.allow=never",
                 *arguments], cwd=self.repository, env=self.environment, check=True,
                capture_output=True, timeout=20,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkspaceError("Dedicated repository Git operation failed; inspect its retained state") from exc

    def _verify_repository(self) -> None:
        # Recheck paths before Git can follow a changed .git directory or include.
        child_path(self.run_root, self.repository.relative_to(self.run_root).as_posix(), ".git")
        snapshot_tree(self.git_directory)
        if any(self.empty_hooks.iterdir()):
            raise WorkspaceError("The controller's empty hooks directory has changed")
        allowed = {"core.repositoryformatversion", "core.filemode", "core.bare", "core.logallrefupdates",
                   "core.ignorecase", "core.precomposeunicode", "core.symlinks", "core.autocrlf"}
        for field in self._git("config", "--local", "--no-includes", "--null", "--list").split(b"\0"):
            if not field:
                continue
            key, _, value = field.decode("utf-8").partition("\n")
            if key not in allowed or (key == "core.bare" and value != "false"):
                raise WorkspaceError("Prepared repositories may contain only basic Git core configuration")
        for name in ("objects/info/alternates", "objects/info/http-alternates", "info/grafts", "shallow"):
            if (self.git_directory / name).exists():
                raise WorkspaceError("Shared objects and rewritten/shallow history are unsupported")
        if self._git("rev-parse", "--show-toplevel").decode().strip() != str(self.repository):
            raise WorkspaceError("Git resolved a different repository")
        if self._git("rev-parse", "--show-object-format").strip() != b"sha1":
            raise WorkspaceError("The prepared offline workflow currently requires SHA-1 Git repositories")

    @property
    def head(self) -> str:
        return self._git("rev-parse", "--verify", "HEAD").decode().strip()

    def _tree(self, commit: str) -> dict[str, str]:
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise WorkspaceError("Expected a full parent commit ID")
        result = {}
        for field in self._git("ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
            if not field:
                continue
            meta, name = field.split(b"\t", 1)
            mode, kind, digest = meta.split()
            name = name.decode("utf-8")
            relative_path(name)
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                raise WorkspaceError("Symlink and submodule commits are unsupported")
            if any(p.casefold() in (".gitattributes", ".gitmodules") for p in name.split("/")):
                raise WorkspaceError("Prepared repositories cannot use Git attributes or submodules")
            result[name] = digest.decode()
        return result

    def verify_clean(self, commit: str | None = None) -> dict[str, Entry]:
        self._verify_repository()
        head = self.head
        if commit is not None and head != commit:
            raise WorkspaceError("The workspace HEAD does not match the expected commit")
        files = snapshot_tree(self.repository, omit_git=True)
        verify_versionable_directories(files)
        expected = self._tree(head)
        actual = {name: entry.digest for name, entry in files.items() if entry.kind == "file"}
        if actual != expected:
            raise WorkspaceError("Workspace has uncommitted, untracked, or ignored files; preserve and reconcile them first")
        if self._git("status", "--porcelain=v1", "--untracked-files=all", "--ignored=matching").strip():
            raise WorkspaceError("The workspace or Git index is not clean")
        return files

    def prepare_parent(self, commit: str, *, known_commits: set[str]) -> PreparedParent:
        self.verify_clean()
        if self.head not in known_commits or commit not in known_commits:
            raise WorkspaceError("Unrecorded Git history requires reconciliation before another candidate")
        self._tree(commit)  # Reject unsupported tree entries before checkout.
        # No force/reset/clean: refuse unexpected work rather than discarding it.
        self._git("checkout", "--detach", commit)
        files = self.verify_clean(commit)
        return PreparedParent(commit, files, snapshot_tree(self.git_directory))

    def inspect_changes(self, parent: PreparedParent, contract: ExperimentContract) -> tuple[dict[str, Entry], tuple[str, ...]]:
        child_path(self.run_root, self.repository.relative_to(self.run_root).as_posix(), ".git")
        if snapshot_tree(self.git_directory) != parent.metadata:
            raise PermissionViolation("The proposer changed Git metadata, index, refs, or repository configuration")
        self._verify_repository()
        files = snapshot_tree(self.repository, omit_git=True)
        changed = tuple(sorted(name for name in files.keys() | parent.files.keys()
                               if files.get(name) != parent.files.get(name)))
        if not changed:
            raise WorkspaceError("The proposer produced no code changes")
        for name in changed:
            if not contract.allows(name):
                raise PermissionViolation(f"Protected or out-of-scope change: {name}")
            before, after = parent.files.get(name), files.get(name)
            if before is not None and after is not None and before.permissions != after.permissions:
                raise PermissionViolation(f"Permission-mode changes are unsupported: {name}")
        verify_versionable_directories(files)
        return files, changed

    def commit_candidate(self, parent: PreparedParent, contract: ExperimentContract,
                         validated_files: dict[str, Entry], parent_experiment: str) -> tuple[str, str]:
        files, changed = self.inspect_changes(parent, contract)
        if files != validated_files:
            raise WorkspaceError("The candidate changed after preflight validation")
        paths = [name for name in changed if (files.get(name) or parent.files[name]).kind == "file"]
        if not paths:
            raise WorkspaceError("A candidate must change versioned file content")
        self._git("add", "--all", "--", *paths)
        staged = self.staged_files()
        actual = {name: entry.digest for name, entry in files.items() if entry.kind == "file"}
        if staged != actual:
            raise WorkspaceError("Staged code differs from the validated filesystem")
        diff = self._git("diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", "--no-renames", parent.commit, "--").decode("utf-8")
        if not diff.strip():
            raise WorkspaceError("The candidate has no Git content diff")
        self._git("commit", "-m", f"Validated offline candidate from {parent_experiment}")
        commit = self.head
        # Retain rejected and interrupted candidates even after detached checkout.
        self._git("update-ref", f"refs/research-intern/candidates/{commit}", commit)
        if self._git("rev-parse", f"{commit}^").decode().strip() != parent.commit:
            raise WorkspaceError("Committed candidate has an unexpected Git parent")
        self.verify_clean(commit)
        return commit, diff

    def staged_files(self) -> dict[str, str]:
        staged = {}
        for field in self._git("ls-files", "--stage", "-z").split(b"\0"):
            if field:
                meta, name = field.split(b"\t", 1)
                _, digest, stage = meta.split()
                if stage != b"0":
                    raise WorkspaceError("Candidate index contains unresolved conflicts")
                staged[name.decode()] = digest.decode()
        return staged
