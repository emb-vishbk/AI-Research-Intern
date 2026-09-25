"""Protected adapter for an existing evaluator that writes plain JSON metrics."""
import argparse
import json
import math
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


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
        for index, value in enumerate(arguments):
            for key, replacement in substitutions.items():
                value = value.replace(key, replacement)
            arguments[index] = value
        # The approved evaluator runs as a separate process using only frozen code.
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
