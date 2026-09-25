"""Local research source handoff and draft budgets; never executes uploaded code."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

from research_intern.contracts.loader import load_contract
from research_intern.contracts.models import CONTRACT_PATH, relative_path
from research_intern.domain.experiments import SliceError, is_finite_number
from research_intern.execution.outputs import read_json
from research_intern.workspace.lock import RunLock
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes
from research_intern.workspace.importing import contract_digest, prepared_workspace, prepare_source

PROJECT_PATH = ".runtime/research-project/repository"
MAX_UPLOAD_FILES = 2000
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class SourceFile:
    path: str
    stream: BinaryIO


@dataclass(frozen=True)
class LoopBudget:
    max_experiments: int
    max_gpu_hours: float
    max_ai_credits: float

    def __post_init__(self) -> None:
        if type(self.max_experiments) is not int or not 0 <= self.max_experiments <= 50:
            raise SliceError("Choose an experiment limit between 0 and 50")
        for value in (self.max_gpu_hours, self.max_ai_credits):
            if not is_finite_number(value) or value < 0:
                raise SliceError("Compute and credit limits must be finite, non-negative numbers")


def source_paths(files: list[SourceFile], *, max_files: int = MAX_UPLOAD_FILES) -> tuple[str, list[str]]:
    """Strip one selected folder; reject ambiguous paths on Windows and Linux."""
    if not 1 <= len(files) <= max_files:
        raise SliceError(f"Select a project containing 1–{max_files} files")
    folder = None
    paths = []
    entries: dict[str, tuple[str, bool]] = {}
    reserved = {"con", "prn", "aux", "nul", "conin$", "conout$"}
    reserved.update(f"{prefix}{number}" for prefix in ("com", "lpt") for number in "123456789¹²³")
    for item in files:
        raw = item.path
        parts = raw.split("/")
        if (len(raw) > 512 or not 2 <= len(parts) <= 32
                or any(c in raw for c in '\\:*?"<>|') or any(ord(c) < 32 or ord(c) == 127 for c in raw)):
            raise SliceError("Upload paths must be relative paths within one selected folder")
        if any(not p or p in (".", "..") or p.endswith((".", " "))
               or len(p.encode("utf-8")) > 255 or p.split(".")[0].casefold() in reserved for p in parts):
            raise SliceError("The selected folder contains an unsafe or unsupported filename")
        if folder is None:
            folder = parts[0]
        if folder != parts[0]:
            raise SliceError("Select one research folder at a time")
        for index in range(1, len(parts)):
            name = "/".join(parts[1:index + 1])
            directory = index < len(parts) - 1
            previous = entries.get(name.casefold())
            if previous is not None and (previous != (name, directory) or not directory):
                raise SliceError("Upload contains duplicate or conflicting file paths")
            entries[name.casefold()] = (name, directory)
        paths.append(relative_path("/".join(parts[1:])))
    return folder, paths


class ProjectStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)

    def root(self) -> Path:
        return child_path(self.workspace, ".runtime", "research-project")

    def inspect(self) -> dict:
        result = {"path": str(self.workspace / PROJECT_PATH), "relative_path": PROJECT_PATH,
                  "name": "Your research project", "status": "missing", "upload_available": False,
                  "execution_available": False, "budget": None, "budget_saved": False,
                  "contract_sha256": None, "workspace_prepared": False, "prepare_available": False,
                  "source_commit": None, "evaluation": None, "evaluation_status": "missing",
                  "evaluation_fingerprint": None,
                  "message": "Select your existing experiment folder or ZIP. The app will inspect its usual layout."}
        try:
            repository = child_path(self.workspace, PROJECT_PATH)
            if not repository.exists() or (repository.is_dir() and not any(repository.iterdir())):
                result["upload_available"] = True
                return result
            metadata = child_path(self.workspace, ".runtime", "research-project", "project.json")
            if metadata.is_file():
                result["name"] = read_json(metadata)["name"]
            if not child_path(repository, CONTRACT_PATH).exists():
                result.update(status="needs_contract", message="Project uploaded. Review the detected files and describe your research goal below.")
                return result
            contract = load_contract(repository)
            digest = contract_digest(repository)
            result.update(status="contract_valid", objective=asdict(contract.rules),
                          contract_sha256=digest, budget=asdict(contract.budget),
                          scope={"editable": list(contract.scope.editable), "protected": list(contract.protected_paths)},
                          message="Contract, research boundaries and Azure job definition checked.",
                          evaluation=asdict(contract.evaluation) if contract.evaluation else None,
                          evaluation_status="unconfirmed" if contract.evaluation else "missing",
                          evaluation_fingerprint=contract.evaluation_fingerprint)
            # During a live run HEAD follows candidates. The original preparation
            # receipt remains the baseline anchor; the live controller owns Git checks.
            live = child_path(self.root(), "live.json")
            if live.is_file():
                prepared = read_json(child_path(self.root(), "workspace.json"))
                config = read_json(live)
                if prepared["contract_sha256"] != digest or config["source_commit"] != prepared["source_commit"]:
                    raise SliceError("Live source preparation identity changed")
                result["budget_saved"] = True
            else:
                prepared = prepared_workspace(self.root(), repository)
            result["prepare_available"] = prepared is None
            if prepared is not None:
                result.update(workspace_prepared=True, source_commit=prepared["source_commit"],
                              preflight_checks=prepared["checks"],
                              message="Source committed and local checks passed. No baseline measurement or experiment has been created.")
                confirmation = child_path(self.root(), "evaluation.json")
                if contract.evaluation is not None and confirmation.exists():
                    expected = self._confirmation(prepared, contract.evaluation_fingerprint)
                    if read_json(confirmation) != expected:
                        raise SliceError("Evaluation confirmation differs from the prepared source; reconcile it before continuing")
                    result["evaluation_status"] = "confirmed"
            settings = child_path(self.workspace, ".runtime", "research-project", "settings.json")
            if settings.is_file():
                saved = read_json(settings)
                if saved["contract_sha256"] == digest:
                    result.update(budget=asdict(LoopBudget(**saved["budget"])), budget_saved=True)
                else:
                    result["message"] = "The contract changed. Review and save the budget again. Live execution is not connected yet."
        except (SliceError, OSError, ValueError, KeyError, TypeError) as exc:
            result.update(status="needs_attention", message=str(exc), workspace_prepared=False,
                          prepare_available=False, evaluation_status="unconfirmed")
        return result

    @staticmethod
    def _confirmation(prepared: dict, fingerprint: str) -> dict:
        return {"source_commit": prepared["source_commit"], "contract_sha256": prepared["contract_sha256"],
                "evaluation_fingerprint": fingerprint, "confirmed": True}

    def prepare(self, contract_sha256: str) -> dict:
        root = self.root()
        root.mkdir(parents=True, exist_ok=True)
        with RunLock(root):
            if child_path(root, "live.json").exists():
                raise SliceError("The live baseline is fixed; preparation cannot replace it")
            prepare_source(root, child_path(root, "repository"), contract_sha256)
        return self.inspect()

    def confirm_evaluation(self, contract_sha256: str, source_commit: str, evaluation_fingerprint: str) -> dict:
        root = self.root()
        root.mkdir(parents=True, exist_ok=True)
        with RunLock(root):
            project = self.inspect()
            if (project["status"] != "contract_valid" or not project["workspace_prepared"]
                    or project["evaluation"] is None
                    or project["contract_sha256"] != contract_sha256
                    or project["source_commit"] != source_commit
                    or project["evaluation_fingerprint"] != evaluation_fingerprint):
                raise SliceError("Evaluation needs a complete protocol and unchanged prepared source. Reload and review it before confirming")
            confirmation = self._confirmation(project, evaluation_fingerprint)
            path = child_path(root, "evaluation.json")
            if path.exists() and read_json(path) != confirmation:
                raise SliceError("An existing evaluation confirmation cannot be overwritten")
            atomic_bytes(path, json.dumps(confirmation, indent=2).encode())
        return self.inspect()

    def save_budget(self, budget: LoopBudget, contract_sha256: str) -> dict:
        root = self.root()
        root.mkdir(parents=True, exist_ok=True)
        with RunLock(root):
            if child_path(root, "live.json").exists():
                raise SliceError("Live limits are fixed; stop and finalize a new research setup to change them")
            project = self.inspect()
            if project["status"] != "contract_valid" or project["contract_sha256"] != contract_sha256:
                raise SliceError("The project contract changed or is invalid. Reload it before saving the budget.")
            path = child_path(root, "settings.json")
            atomic_bytes(path, json.dumps({"contract_sha256": contract_sha256, "budget": asdict(budget)},
                                         allow_nan=False, indent=2).encode())
        return self.inspect()

    def import_folder(self, files: list[SourceFile]) -> dict:
        folder, paths = source_paths(files)
        root = self.root()
        root.mkdir(parents=True, exist_ok=True)
        with RunLock(root):
            target = child_path(root, "repository")
            if target.exists() and (not target.is_dir() or any(target.iterdir())):
                raise SliceError("A research project is already loaded. Existing files will not be overwritten.")
            with tempfile.TemporaryDirectory(dir=root, prefix="upload-") as temporary:
                staging = Path(temporary) / "repository"
                staging.mkdir()
                total = 0
                for item, name in zip(files, paths, strict=True):
                    destination = child_path(staging, name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with destination.open("xb") as output:
                        while chunk := item.stream.read(1024 * 1024):
                            total += len(chunk)
                            if total > MAX_UPLOAD_BYTES:
                                raise SliceError("Source upload exceeds 64 MiB. Keep datasets, weights and environments outside the source folder.")
                            output.write(chunk)
                if (staging / CONTRACT_PATH).exists():
                    load_contract(staging)
                # Recheck immediately before publishing; only an empty slot may be removed.
                target = child_path(root, "repository")
                if target.exists():
                    target.rmdir()
                staging.rename(target)
            atomic_bytes(child_path(root, "project.json"), json.dumps({"name": folder}).encode())
        return self.inspect()
