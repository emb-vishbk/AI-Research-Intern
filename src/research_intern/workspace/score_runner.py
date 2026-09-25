"""Protected adapter for an existing evaluator that writes plain JSON metrics."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


def artifact_file(outputs, names):
    matches = sorted(p for p in outputs.rglob("*") if p.is_file() and p.name in names)
    if len(matches) != 1:
        if not matches:
            raise RuntimeError("Saved predictions/checkpoint are missing from the job outputs. Expected: " + ", ".join(names))
        raise RuntimeError("Multiple saved prediction/checkpoint files match the evaluator; select the run's evaluation output folder.")
    return matches[0]


def safe_path(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise RuntimeError("Evaluation input must stay inside its project folder")
    return path


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    spec = json.loads((root / ".research_intern/scoring.json").read_text())
    request = json.loads(Path(args.request).read_text())
    with tempfile.TemporaryDirectory(prefix="evaluation-") as temporary:
        work = Path(temporary) / "source"
        shutil.copytree(root, work)
        for name, expected in spec.get("assets", {}).items():
            source = safe_path(Path(request["asset_root"]), name)
            target = safe_path(work, name)
            if target.exists():
                # Some small reference files were promoted into the frozen bundle.
                source = target
            if not source.is_file() or source.stat().st_size != expected["bytes"] or file_digest(source) != expected["sha256"]:
                raise RuntimeError("Retained evaluation input changed: " + name)
            if source != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                if file_digest(target) != expected["sha256"]:
                    raise RuntimeError("Evaluation input changed during copying: " + name)
        metrics_file = Path(temporary) / "metrics.json"
        fallback = (work / spec["metrics_file"]).resolve()
        if not fallback.is_relative_to(work) or fallback == work:
            raise RuntimeError("Evaluation metrics must stay inside the evaluation working folder")
        if fallback.exists():
            raise RuntimeError("Select a fresh evaluation output filename, not an existing source file")
        arguments = shlex.split(spec["command"])
        if arguments and arguments[0] in {"python", "python3", "{python}"}:
            arguments[0] = sys.executable
        substitutions = {"{outputs}": str(Path(args.outputs).resolve()), "{result}": str(metrics_file),
                         "{reference}": str(work / spec["reference"]), "{python}": sys.executable}
        for name, binding in spec.get("bindings", {}).items():
            substitutions["{" + name + "}"] = str(safe_path(work, binding))
        for name, candidates in spec.get("artifacts", {}).items():
            substitutions["{" + name + "}"] = str(artifact_file(Path(args.outputs), candidates))
        for index, value in enumerate(arguments):
            for key, replacement in substitutions.items():
                value = value.replace(key, replacement)
            arguments[index] = value
        # The approved evaluator runs as a separate process using only frozen code.
        if arguments[0] == sys.executable and len(arguments) > 1 and arguments[1].endswith(".py"):
            roots = [str((work / p).resolve()) for p in spec.get("code_roots", [".", "src"])]
            if any(not Path(p).is_relative_to(work) for p in roots):
                raise RuntimeError("Evaluation imports must stay inside frozen source")
            driver = "import runpy,sys,json; sys.path[:0]=json.loads(sys.argv[1]); sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')"
            arguments = [sys.executable, "-I", "-B", "-c", driver, json.dumps(roots), *arguments[1:]]
        subprocess.run(arguments, cwd=work, check=True)
        if not metrics_file.exists():
            metrics_file = fallback
        payload = json.loads(metrics_file.read_text())
        metrics = payload.get("metrics", payload)
        metrics = {k: float(v) for k, v in metrics.items() if type(v) in (int, float) and math.isfinite(v)}
        identity = {key: request[key] for key in ("experiment_id", "parent_experiment", "git_commit", "job_id", "evaluation_fingerprint")}
        Path(args.result).write_text(json.dumps({"identity": identity, "metrics": metrics}, allow_nan=False))


if __name__ == "__main__":
    main()
