"""Inspect ordinary project layouts without importing or executing their code."""
from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

import yaml

from research_intern.domain.experiments import SliceError
from research_intern.workspace.paths import child_path
from research_intern.workspace.project import SourceFile, source_paths, MAX_UPLOAD_BYTES
from research_intern.workspace.lock import RunLock
from research_intern.workspace.recovery import atomic_bytes

MAX_PROJECT_BYTES = 2 * 1024**3
MAX_PROJECT_FILES = 30000
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
             ".mypy_cache", ".ruff_cache", ".tox", ".cache", ".runtime", ".idea", ".vscode",
             ".azure", ".ssh", ".aws", ".copilot", ".codex"}
SOURCE_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".txt", ".md", ".rst", ".cfg", ".ini",
                   ".sh", ".bat", ".cmd", ".ps1", ".ipynb", ".c", ".cpp", ".h", ".cu", ".lock"}
ASSET_DIRS = {"data", "datasets", "weights", "checkpoints", "outputs", "results", "runs", "logs", "artifacts"}


def exclusion(name: str) -> str | None:
    parts = PurePosixPath(name).parts
    lowered = [p.casefold() for p in parts]
    filename = lowered[-1]
    if any(p in SKIP_DIRS or p.endswith(".egg-info") for p in lowered[:-1]):
        return "Environment, cache or private application state"
    if (filename == ".env" or filename.startswith(".env.") and filename not in {".env.example", ".env.sample", ".env.template"}
            or filename in {"id_rsa", "id_ed25519", "credentials", "credentials.json", "secrets.json"}
            or filename.endswith((".pem", ".key", ".pfx", ".p12", ".cer"))):
        return "Credential or certificate file"
    if filename.endswith((".pyc", ".pyo", ".tmp", ".swp")) or filename in {".ds_store", "thumbs.db"}:
        return "Generated temporary file"
    return None


def is_asset(name: str) -> bool:
    path = PurePosixPath(name)
    return (any(p.casefold() in ASSET_DIRS for p in path.parts[:-1])
            or path.suffix.casefold() not in SOURCE_SUFFIXES and path.name.casefold() not in
            {"dockerfile", "makefile", "license", ".gitignore", ".amlignore", ".dockerignore"})


def _json(path: Path):
    if not path.is_file() or path.stat().st_size > 4 * 1024**2:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        return None


def metric_values(value) -> dict:
    """Keep numeric observations only; never surface arbitrary JSON/config values."""
    if not isinstance(value, dict):
        return {}
    nested = value.get("metrics", value)
    if not isinstance(nested, dict):
        return {}
    return {k: float(v) for k, v in nested.items() if isinstance(k, str) and len(k) <= 128
            and type(v) in (int, float) and math.isfinite(v)}


def inspect_project(repository: Path, assets: Path | None = None) -> dict:
    files = sorted(p.relative_to(repository).as_posix() for p in repository.rglob("*")
                   if p.is_file() and not p.is_symlink() and not exclusion(p.relative_to(repository).as_posix()))
    jobs, metrics, hints, problems = [], [], {}, []
    paths = [(repository, name) for name in files]
    if assets and assets.is_dir():
        paths += [(assets, p.relative_to(assets).as_posix()) for p in assets.rglob("*") if p.is_file() and not p.is_symlink()]
    evaluators, splits, training, dependencies = [], [], [], []
    for root, name in paths:
        path = root / name
        lower = name.casefold()
        if path.suffix == ".py":
            if re.search(r"(^|[/_])(eval|evaluate|evaluation|score|scorer|live_score)([/_.]|$)", lower):
                evaluators.append(name)
            elif re.search(r"(^|[/_])(train|training|fit|finetune)([/_.]|$)", lower):
                training.append(name)
        if re.search(r"(manifest|split|validation|ground.?truth|reference)", lower) and path.suffix in {".json", ".txt", ".csv"}:
            splits.append(name)
        if path.name in {"pyproject.toml", "requirements.txt", "environment.yml", "environment.yaml", "Dockerfile", "setup.py"}:
            dependencies.append(name)
        if path.suffix == ".json":
            if re.search(r"(metric|result|score|history)", path.name, re.I):
                found = metric_values(_json(path))
                if found:
                    metrics.append({"path": name, "values": found})
            if path.name.casefold() in {"config.json", "azure.json", "workspace.json"}:
                config = _json(path)
                if isinstance(config, dict) and {"subscription_id", "resource_group", "workspace_name"} <= config.keys():
                    for key in ("subscription_id", "resource_group", "workspace_name"):
                        if isinstance(config[key], str) and len(config[key]) < 200:
                            hints.setdefault(key, config[key])
        if root != repository or path.suffix not in {".yaml", ".yml"} or path.stat().st_size > 1024**2:
            continue
        try:
            job = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeError, RecursionError):
            problems.append(f"Could not read YAML: {name}")
            continue
        if not isinstance(job, dict) or not ("command" in job or "jobs" in job):
            continue
        code = job.get("code", ".")
        code_root = None
        if isinstance(code, str):
            candidate = (path.parent / code).resolve()
            if candidate.is_relative_to(repository.resolve()) and candidate.is_dir():
                code_root = candidate.relative_to(repository.resolve()).as_posix()
        jobs.append({"path": name, "type": str(job.get("type", "command")),
                     "compute": job.get("compute") if isinstance(job.get("compute"), str) else "",
                     "environment": job.get("environment") if isinstance(job.get("environment"), str) else "Inline environment",
                     "experiment_name": str(job.get("experiment_name", ""))[:200],
                     "job_name": str(job.get("name", ""))[:200], "code_root": code_root,
                     "inputs": list(job.get("inputs", {})) if isinstance(job.get("inputs", {}), dict) else [],
                     "outputs": list(job.get("outputs", {})) if isinstance(job.get("outputs", {}), dict) else [],
                     "supported": job.get("type", "command") == "command" and isinstance(job.get("command"), str) and code_root is not None})
    # A prepared project is supported too; users are never required to supply one.
    contract_path = repository / ".research_intern/contract.yaml"
    contract = None
    if contract_path.is_file():
        try:
            contract = yaml.safe_load(contract_path.read_text())
        except (yaml.YAMLError, UnicodeError):
            pass
    return {"files": files, "jobs": jobs, "training": training, "evaluators": evaluators,
            "validation_files": splits, "dependencies": dependencies, "metrics": metrics,
            "azure_hints": hints, "problems": problems, "existing_contract": contract if isinstance(contract, dict) else None}


class ProjectImporter:
    def __init__(self, workspace: Path):
        self.root = child_path(workspace, ".runtime/research-project")

    def import_files(self, files: list[SourceFile], *, archive=False) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        with RunLock(self.root), ExitStack() as opened:
            excluded = Counter()
            target = child_path(self.root, "repository")
            if target.exists() and any(target.iterdir()):
                raise SliceError("A project is already loaded. Its source and research history will not be overwritten.")
            if archive:
                if len(files) != 1:
                    raise SliceError("Select one project ZIP file")
                try:
                    bundle = opened.enter_context(zipfile.ZipFile(files[0].stream))
                    entries = bundle.infolist()
                    if len(entries) > 250000:
                        raise SliceError("ZIP contains too many entries to inspect safely")
                    members = []
                    for entry in entries:
                        if entry.is_dir():
                            continue
                        mode = stat.S_IFMT(entry.external_attr >> 16)
                        if entry.flag_bits & 1 or mode not in (0, stat.S_IFREG):
                            raise SliceError("Encrypted ZIP entries and symbolic links are not supported")
                        # A synthetic folder gives root-level and nested ZIPs the same validation.
                        members.append(entry)
                    names = [m.filename for m in members]
                    if not names or any(n.startswith("/") or "\\" in n or any(p in {"", ".", ".."} for p in n.split("/")) for n in names):
                        raise SliceError("ZIP paths must stay inside the project")
                    roots = {PurePosixPath(n).parts[0] for n in names}
                    strip_root = len(roots) == 1 and all("/" in n for n in names)
                    folder = next(iter(roots)) if strip_root else Path(files[0].path).stem
                    files, declared_bytes = [], 0
                    for name, entry in zip(names, members, strict=True):
                        name = name.split("/", 1)[1] if strip_root else name
                        reason = exclusion(name)
                        if reason:
                            excluded[reason] += 1
                            continue
                        declared_bytes += entry.file_size
                        if len(files) >= MAX_PROJECT_FILES or declared_bytes > MAX_PROJECT_BYTES:
                            raise SliceError("Retained ZIP contents exceed the 2 GiB / 30,000-file project limit")
                        files.append(SourceFile(folder + "/" + name, opened.enter_context(bundle.open(entry))))
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
                    raise SliceError("The project ZIP could not be read. Use a regular, unencrypted ZIP archive.") from exc
            # .git is excluded before the stricter source-policy path checker.
            kept = []
            for item in files:
                # Validate traversal even for files which will not be retained.
                parts = item.path.split("/")
                if len(parts) < 2 or any(p in {"", ".", ".."} for p in parts) or "\\" in item.path or item.path.startswith("/"):
                    raise SliceError("Project files must stay inside the selected folder")
                reason = exclusion("/".join(parts[1:]))
                if reason:
                    excluded[reason] += 1
                else:
                    kept.append(item)
            folder, paths = source_paths(kept, max_files=MAX_PROJECT_FILES)
            with tempfile.TemporaryDirectory(dir=self.root, prefix="import-") as temporary:
                stage = Path(temporary)
                source, assets = stage / "repository", stage / "assets"
                source.mkdir()
                assets.mkdir()
                source_bytes = total = 0
                asset_manifest = {}
                for item, name in zip(kept, paths, strict=True):
                    asset = is_asset(name)
                    destination = child_path(assets if asset else source, name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest, size = hashlib.sha256(), 0
                    with destination.open("xb") as output:
                        while chunk := item.stream.read(1024**2):
                            size += len(chunk)
                            total += len(chunk)
                            if not asset:
                                source_bytes += len(chunk)
                            if total > MAX_PROJECT_BYTES or source_bytes > MAX_UPLOAD_BYTES:
                                raise SliceError("Project exceeds 2 GiB of retained files or 64 MiB of editable source")
                            digest.update(chunk)
                            output.write(chunk)
                    if asset:
                        asset_manifest[name] = {"sha256": digest.hexdigest(), "bytes": size}
                analysis = inspect_project(source, assets)
                if not analysis["files"]:
                    raise SliceError("No project source files were found after excluding environments and private files")
                if target.exists():
                    target.rmdir()
                asset_target = self.root / "assets"
                if asset_target.exists():
                    raise SliceError("An earlier asset import needs reconciliation before importing again")
                assets.rename(asset_target)
                try:
                    source.rename(target)
                except BaseException:
                    asset_target.rename(assets)
                    raise
                metadata = {"name": folder, "excluded": dict(excluded), "source_bytes": source_bytes,
                            "retained_bytes": total, "assets": asset_manifest}
                atomic_bytes(self.root / "project.json", json.dumps(metadata, indent=2).encode())
                atomic_bytes(self.root / "discovery.json", json.dumps(analysis, indent=2).encode())
            return analysis

    def inspect(self) -> dict:
        repository = self.root / "repository"
        if not repository.is_dir():
            raise SliceError("Upload or select your experiment folder first")
        analysis = inspect_project(repository, self.root / "assets")
        atomic_bytes(self.root / "discovery.json", json.dumps(analysis, indent=2).encode())
        return analysis
