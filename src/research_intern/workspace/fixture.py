"""A new, local toy Git repository for offline demos, never a research-workspace manager."""

import os
import shutil
import subprocess
from pathlib import Path

from research_intern.domain.experiments import SliceError
from research_intern.workspace.paths import child_path


class GitFixture:
    def __init__(self, root: Path):
        executable = shutil.which("git")
        if executable is None:
            raise SliceError("The offline demo needs an already installed Git executable")
        self.executable = executable
        self.repository = child_path(root, "fixture_repo")
        self.repository.mkdir(exist_ok=False)
        self.template = child_path(root, "empty_git_template")
        self.template.mkdir(exist_ok=False)
        temporary = child_path(root, "git_tmp")
        temporary.mkdir(exist_ok=False)
        # Only these child processes use the simulation identity and configuration.
        # Never inherit repository redirects, credentials, hooks, signing, or filters.
        self.environment = {key: value for key, value in os.environ.items()
                            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
        self.environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                                GIT_TERMINAL_PROMPT="0", TMPDIR=str(temporary),
                                TMP=str(temporary), TEMP=str(temporary))
        self._git("init", "--initial-branch=main", f"--template={self.template}")

    def _git(self, *arguments: str) -> str:
        try:
            completed = subprocess.run(
                [self.executable, "-c", "user.name=Offline Simulation", "-c",
                 "user.email=simulation@example.invalid", "-c", "commit.gpgSign=false",
                 "-c", "core.autocrlf=false", "-c", f"core.hooksPath={self.template}",
                 *arguments], cwd=self.repository, env=self.environment, check=True,
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SliceError("Local fixture Git setup failed; no application Git operation was requested") from exc
        return completed.stdout.strip()

    def commit_weight_decay(self, value: float, message: str) -> str:
        training = child_path(self.repository, "train.py")
        training.write_text(f"# Synthetic fixture only; never executed.\nWEIGHT_DECAY = {value}\n", encoding="utf-8")
        self._git("add", "--", "train.py")
        self._git("commit", "-m", message)
        return self._git("rev-parse", "HEAD")

    def diff(self, parent_commit: str, candidate_commit: str) -> str:
        return self._git("diff", "--no-ext-diff", "--no-textconv", parent_commit, candidate_commit,
                         "--", "train.py") + "\n"


def prepare_git_fixture(root: Path) -> tuple[str, str, str]:
    fixture = GitFixture(root)
    baseline = fixture.commit_weight_decay(0.0, "Synthetic baseline for offline execution demo")
    candidate = fixture.commit_weight_decay(0.01, "Synthetic candidate for offline execution demo")
    return baseline, candidate, fixture.diff(baseline, candidate)
