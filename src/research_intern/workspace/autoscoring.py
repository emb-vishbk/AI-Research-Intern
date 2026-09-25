"""Infer evaluator wiring from source syntax and job inputs, without executing code.

Only unambiguous argparse interfaces are adapted. Scientific evaluation remains
the project's own implementation; missing inputs are never replaced with metrics
reported by the training process.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import shlex
import sys
import tomllib

import yaml

from research_intern.domain.experiments import SliceError
from research_intern.workspace.paths import child_path

REFERENCE = {"manifest", "reference", "validation-file", "validation-split", "split", "ground-truth", "annotations"}
DATA = {"data-root", "dataset", "dataset-root", "data-dir"}
OUTPUT = {"output", "output-file", "metrics-file", "result"}
ARTIFACTS = {"predictions": ("predictions.json", "predictions.csv", "predictions.npz"),
             "checkpoint": ("checkpoint.pt", "best.pt", "model.pt", "checkpoint.pth")}


def source_file(repository, assets, name):
    path = child_path(repository, name)
    return path if path.is_file() else child_path(assets, name)


def tree(path):
    if not path.is_file() or path.stat().st_size > 1024**2:
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeError, RecursionError):
        return None


def arguments(parsed):
    """Return literal argparse declarations, including unsupported required ones."""
    result = []
    for node in ast.walk(parsed) if parsed else ():
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument":
            continue
        flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not flags:
            continue
        flag = next((f for f in flags if f.startswith("--")), flags[0])
        keywords = {k.arg: k.value.value for k in node.keywords if isinstance(k.value, ast.Constant)}
        result.append({"flag": flag, "name": flag.lstrip("-").replace("_", "-"),
                       "required": keywords.get("required") is True or not flag.startswith("-"),
                       "default": keywords.get("default"), "action": keywords.get("action"),
                       "nargs": keywords.get("nargs")})
    return result


def evaluator_candidates(repository, assets, names):
    result = []
    for name in names:
        if name.startswith(".research_intern/") or not name.endswith(".py"):
            continue
        flags = {a["name"] for a in arguments(tree(source_file(repository, assets, name)))}
        if {"outputs", "request", "result"} <= flags or (flags & ARTIFACTS.keys() and flags & OUTPUT):
            result.append(name)
    named = [name for name in result if re.search(r"(^|_)(eval|evaluate|evaluation|score|scorer|live_score)($|_)", Path(name).stem)]
    return sorted(named or result)


def literal_path(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = literal_path(node.left), literal_path(node.right)
        if right:
            return (left + "/" if left else "") + right
    return None


def dependencies(repository, assets, evaluator, code_root="."):
    """Close over local imports, package initializers and literal reference files."""
    roots = list(dict.fromkeys([code_root, (Path(code_root) / "src").as_posix(), ".", "src", Path(evaluator).parent.as_posix()]))
    pending, protected, external = [evaluator], set(), set()

    def module_files(module):
        for root in roots:
            base = Path(root) / module.replace(".", "/")
            for path in (base.with_suffix(".py"), base / "__init__.py"):
                name = path.as_posix()
                if source_file(repository, assets, name).is_file():
                    files = [name]
                    for parent in path.parents:
                        if parent.as_posix() == root or parent == Path("."):
                            break
                        init = (parent / "__init__.py").as_posix()
                        if source_file(repository, assets, init).is_file():
                            files.append(init)
                    return files
        return []

    while pending:
        name = pending.pop()
        if name in protected:
            continue
        protected.add(name)
        parsed = tree(source_file(repository, assets, name))
        for node in ast.walk(parsed) if parsed else ():
            modules = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    relative = Path(name).parent
                    for _ in range(node.level - 1):
                        relative = relative.parent
                    parts = relative.parts
                    root = next((Path(r).parts for r in roots if parts[:len(Path(r).parts)] == Path(r).parts and r != "."), ())
                    base = ".".join((*parts[len(root):], *([base] if base else [])))
                modules = [base] + [base + "." + a.name for a in node.names if a.name != "*"]
            for module in modules:
                local = module_files(module) if module else []
                if local:
                    pending.extend(local)
                elif module and not module_files(module.split(".")[0]) and not any(
                        (base / root / module.split(".")[0]).is_dir() for base in (repository, assets) for root in roots):
                    external.add(module.split(".")[0])
            value = literal_path(node)
            if value and Path(value).suffix in {".json", ".yaml", ".yml", ".toml", ".csv", ".txt"}:
                for base in (".", code_root, str(Path(name).parent)):
                    path = Path(base) / value
                    if path.is_absolute() or ".." in path.parts:
                        continue
                    if source_file(repository, assets, path.as_posix()).is_file():
                        protected.add(path.as_posix())
    external -= sys.stdlib_module_names | {"__future__"}
    return sorted(protected), sorted(external), roots


def runtime_spec(repository, imports):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    declared, python, files = [], ">=3.11", []
    project = repository / "pyproject.toml"
    if project.is_file():
        try:
            value = tomllib.loads(project.read_text())["project"]
            declared = value.get("dependencies", [])
            python = value.get("requires-python", python)
            files.append("pyproject.toml")
        except (ValueError, KeyError, TypeError) as exc:
            raise SliceError("The project's pyproject.toml cannot be read for evaluation dependencies") from exc
    if not declared and (repository / "requirements.txt").is_file():
        declared = [line.split(" #", 1)[0].strip() for line in (repository / "requirements.txt").read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        files.append("requirements.txt")
    aliases = {"PIL": "pillow", "yaml": "pyyaml", "cv2": "opencv-python", "sklearn": "scikit-learn"}
    needed = {canonicalize_name(aliases.get(name, name)) for name in imports}
    requirements = []
    for value in declared:
        try:
            req = Requirement(value)
        except ValueError as exc:
            raise SliceError("Evaluation dependencies need standard package declarations in pyproject.toml or requirements.txt") from exc
        if canonicalize_name(req.name) in needed:
            if req.url:
                raise SliceError(f"Evaluation dependency {req.name} needs a package version instead of a direct URL")
            requirements.append(str(req))
    return {"requires_python": python, "requirements": sorted(requirements), "imports": imports}, files


def discover(repository, assets, state, metadata):
    """An inspectable plan. No commands, downloads or source imports are run here."""
    repository, assets = Path(repository), Path(assets)
    names = sorted({p.relative_to(base).as_posix() for base in (repository, assets) if base.is_dir()
                    for p in base.rglob("*.py") if ".git" not in p.parts})
    candidates = evaluator_candidates(repository, assets, names)
    evaluator = state.get("evaluation_file") or (candidates[0] if len(candidates) == 1 else "")
    result = {"status": "needs_input", "evaluator": evaluator, "candidates": candidates, "protected": [], "message": ""}
    def missing(message):
        result["message"] = message
        return result
    if not evaluator:
        return missing("More than one scoring script was found. Select the evaluator to use." if candidates else
                       "No runnable evaluation script was found in this project. Include the script that scores validation results.")
    declarations = arguments(tree(source_file(repository, assets, evaluator)))
    flags = {a["name"] for a in declarations}
    native = {"outputs", "request", "result"} <= flags
    if not declarations and not state.get("evaluation_command"):
        return missing(f"The selected evaluator ({evaluator}) has no discoverable command-line inputs. Include its scoring entry point.")
    job_path = state.get("job_config")
    job = yaml.safe_load(child_path(repository, job_path).read_text()) if job_path else {}
    job = job if isinstance(job, dict) else {}
    code_root = ((Path(job_path).parent if job_path else Path(".")) / job.get("code", ".")).as_posix()
    if ".." in Path(code_root).parts or Path(code_root).is_absolute():
        return missing("The selected YAML must use a code folder inside the project.")
    protected, imports, roots = dependencies(repository, assets, evaluator, code_root)
    runtime, dependency_files = runtime_spec(repository, imports)
    protected += dependency_files
    protected += [p.relative_to(repository).as_posix() for p in repository.rglob("*")
                  if p.is_file() and p.name.casefold() in {".gitignore", ".gitattributes", ".gitmodules", ".amlignore", ".dockerignore"}
                  and ".git" not in p.parts]
    result.update(protected=sorted(set(protected)), runtime=runtime, code_roots=roots)
    reference = state.get("validation_file", "")
    if not reference:
        matches = set()
        for base in (repository, assets):
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                name = path.relative_to(base).as_posix()
                if path.is_file() and path.suffix in {".json", ".csv", ".txt"} and re.search(r"(manifest|split|validation|ground.?truth|reference)", path.name, re.I) and ".research_intern" not in path.parts:
                    matches.add(name)
        if len(matches) == 1:
            reference = matches.pop()
    result["reference"] = reference
    if not reference or not source_file(repository, assets, reference).is_file():
        return missing("Select the validation split or reference: no single matching reference file was found in the project.")
    result["protected"] = sorted(set(result["protected"] + [reference]))
    spec = {"reference": reference, "metrics_file": state.get("evaluation_metrics") or "metrics.json",
            "code_roots": roots, "assets": {}, "bindings": {}, "artifacts": {}, "runtime": runtime}
    argv = ["{python}", evaluator]
    for argument in declarations if not native and not state.get("evaluation_command") else ():
        name, flag = argument["name"], argument["flag"]
        if name in REFERENCE:
            value = "{reference}"
        elif name in OUTPUT:
            value = "{result}"
        elif name in ARTIFACTS:
            value = "{" + name + "}"
            spec["artifacts"][name] = list(ARTIFACTS[name])
        elif name in DATA or name in {k.replace("_", "-") for k in job.get("inputs", {})}:
            matches = []
            for key, binding in job.get("inputs", {}).items():
                if key.replace("_", "-") != name and not (name in DATA and key.replace("_", "-") in DATA | {"data"}):
                    continue
                if isinstance(binding, dict) and isinstance(binding.get("path"), str):
                    raw = binding["path"]
                    path = ((repository / job_path).parent / raw).resolve()
                    if ":" not in raw and path.is_relative_to(repository.resolve()):
                        matches.append(path.relative_to(repository.resolve()).as_posix())
            if len(matches) != 1:
                return missing(f"The evaluator needs {flag}, but the YAML has no single local data input for it. Include the evaluation data with the project and reference it in the YAML.")
            binding = matches[0]
            retained = {n: v for n, v in metadata.get("assets", {}).items() if n == binding or n.startswith(binding + "/")}
            sources = [p.relative_to(repository).as_posix() for p in (repository / binding).rglob("*") if p.is_file()] if (repository / binding).is_dir() else ([binding] if (repository / binding).is_file() else [])
            if not retained and not sources:
                return missing(f"Evaluation data is missing from the uploaded project: {binding}")
            result["protected"] = sorted(set(result["protected"] + sources))
            spec["assets"].update(retained)
            spec["bindings"][name] = binding
            value = "{" + name + "}"
        elif argument["required"]:
            return missing(f"The evaluator needs {flag}; no matching input was found in the YAML. Add that input to the project job definition.")
        else:
            continue
        if argument["action"] or argument["nargs"] not in (None, 1):
            return missing(f"The evaluator's {flag} uses an unsupported argument shape. Its invocation cannot be inferred safely.")
        argv.extend([flag, value] if flag.startswith("-") else [value])
    if not native and not state.get("evaluation_command") and not (flags & ARTIFACTS.keys() and flags & OUTPUT):
        return missing(f"The selected evaluator ({evaluator}) does not declare prediction/checkpoint inputs and a JSON result output.")
    command = state.get("evaluation_command", "").strip() or ("" if native else shlex.join(argv))
    spec["command"] = command
    result.update(status="ready", native=native and not command, command=command, spec=spec,
                  message=f"Evaluation detected: {evaluator} using {reference}. Inputs, result collection and Python dependencies will be prepared automatically.")
    return result
