"""Prepare a project-owned scoring environment, independent of the app's Python."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from research_intern.domain.experiments import SliceError
from research_intern.evaluation.bootstrap import install_uv
from research_intern.workspace.recovery import atomic_bytes


def probe(python, spec, diagnostic=None):
    """Check Python, declared versions and actual third-party imports in isolation."""
    script = ("import importlib,importlib.metadata as m,json,sys; "
              "s=json.loads(sys.argv[1]); "
              "[importlib.import_module(n) for n in s['imports']]; "
              "print(json.dumps({'python':sys.version.split()[0],"
              "'versions':{n:m.version(n) for n in s['packages']}}))")
    packages = [Requirement(r).name for r in spec["requirements"]]
    try:
        result = subprocess.run([str(python), "-I", "-B", "-c", script,
                                 json.dumps({"imports": spec["imports"], "packages": packages})],
                                capture_output=True, text=True, timeout=120, check=True,
                                env={k: v for k, v in os.environ.items() if k.upper() in
                                     {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "TEMP", "TMP"}})
        value = json.loads(result.stdout.splitlines()[-1])
        if value["python"] not in SpecifierSet(spec["requires_python"]):
            return None
        if any(value["versions"][Requirement(r).name] not in Requirement(r).specifier for r in spec["requirements"]):
            return None
        return value
    except (OSError, subprocess.SubprocessError, ValueError, IndexError, KeyError) as exc:
        if diagnostic is not None:
            with diagnostic.open("a", encoding="utf-8") as log:
                log.write("\nEvaluation import check failed:\n" + str(getattr(exc, "stderr", "") or type(exc).__name__)[:16000] + "\n")
        return None


def run_command(arguments, log, environment, cwd, *, phase="dependency installation"):
    try:
        with log.open("ab") as output:
            output.write(("\nEvaluation setup: " + phase + "\n").encode())
            output.flush()
            subprocess.run([str(a) for a in arguments], cwd=cwd, env=environment, stdout=output,
                           stderr=subprocess.STDOUT, timeout=1800, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SliceError(f"Automatic evaluation setup failed during {phase}. "
                         "Check scoring-environments/setup.log for the exact error and retry preparation; "
                         "your project and experiment budget are unchanged.") from exc


def prepare_runtime(root, spec, override=""):
    root = Path(root)
    if override:
        if not Path(override).is_absolute() or not probe(override, spec):
            raise SliceError("The specified evaluation Python does not meet the project's Python/dependency requirements. Clear the override to prepare it automatically.")
        return override
    # Plain stdlib evaluators need no downloads or additional environment.
    if not spec["imports"] and probe(sys.executable, spec):
        return sys.executable
    key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:20]
    cache = root / "scoring-environments"
    cache.mkdir(exist_ok=True)
    target = cache / key
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    receipt = cache / (key + ".json")
    if receipt.is_file() and python.is_file() and probe(python, spec):
        return str(python)
    environment = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "HOME", "USERPROFILE", "TEMP", "TMP",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_CERT"}}
    environment.update(UV_CACHE_DIR=str(cache / "cache"), UV_PYTHON_INSTALL_DIR=str(cache / "python"),
                       UV_NO_CONFIG="1", UV_NATIVE_TLS="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    log = cache / "setup.log"
    status = root / "scoring-environment.json"
    atomic_bytes(status, json.dumps({"status": "preparing", "message": "Preparing Python and the evaluator's dependencies. First-time downloads may take several minutes."}).encode())
    try:
        # The official standalone binary works even when this Python has neither
        # pip nor ensurepip. Failed legacy tools/ venvs do not block this path.
        uv = install_uv(cache, log, environment)
        if not python.is_file():
            # Prefer a supported stable Python shared by the project's wheels.
            versions = SpecifierSet(spec["requires_python"])
            selected = next((f"3.{n}" for n in (12, 13, 11, 10, 14) if f"3.{n}.0" in versions or f"3.{n}.9" in versions), None)
            if selected is None:
                raise SliceError(f"No supported evaluation Python matches the project's requirement: {spec['requires_python']}")
            run_command([uv, "venv", "--python", selected, str(target)], log, environment, cache,
                        phase="compatible Python environment creation")
        torch = [r for r in spec["requirements"] if canonicalize_name(Requirement(r).name) in {"torch", "torchvision", "torchaudio"}]
        other = [r for r in spec["requirements"] if r not in torch]
        # Independent scoring is local CPU work. Do not download a CUDA stack.
        for requirements, index in ((torch, "https://download.pytorch.org/whl/cpu"), (other, "https://pypi.org/simple")):
            if requirements:
                run_command([uv, "pip", "install", "--python", python, "--only-binary=:all:",
                             "--index-url", index, *requirements], log, environment, cache)
        checked = probe(python, spec, log)
        if checked is None:
            raise SliceError("The evaluation environment was created, but its import/version check failed. Check the evaluator's declared dependencies and scoring-environments/setup.log.")
        run_command([uv, "pip", "freeze", "--python", python], log, environment, cache)
        atomic_bytes(receipt, json.dumps({"spec": spec, "verified": checked}).encode())
        atomic_bytes(status, json.dumps({"status": "ready", "message": "Evaluation Python and dependencies are ready."}).encode())
        return str(python)
    except Exception:
        atomic_bytes(status, json.dumps({"status": "failed", "message": "Evaluation environment setup needs attention. Its local setup log contains the details."}).encode())
        raise
