"""Standard-library adapter copied into an imported project's protected app files.

Runs the user's original Azure command and preserves its outputs. Scientific
scores are computed later by the frozen evaluator, never inferred here.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--original-output", nargs=2, action="append", default=[])
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "logs").mkdir(exist_ok=True)
    (destination / "artifacts").mkdir(exist_ok=True)
    with (destination / "logs/training.log").open("wb") as log:
        process = subprocess.Popen(args.command, cwd=args.cwd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        while chunk := process.stdout.read1(65536):
            log.write(chunk)
            log.flush()
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
        code = process.wait()
    for name, folder in args.original_output:
        source = Path(folder).resolve()
        if not source.is_dir() or source == destination:
            continue
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise RuntimeError("Original outputs must not overlap the collection directory")
        target = destination if len(args.original_output) == 1 else destination / "outputs" / name
        for path in source.rglob("*"):
            if path.is_symlink():
                raise RuntimeError("Training outputs must not contain symbolic links")
            relative = path.relative_to(source)
            if relative.as_posix() in {"run.json", "logs/training.log"}:
                continue
            output = target / relative
            if path.is_dir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, output)
    (destination / "run.json").write_text(json.dumps({
        "schema_version": "1.0", "experiment_id": args.experiment_id,
        "parent_experiment": None if args.parent == "none" else args.parent,
        "source_commit": args.commit, "evaluation_fingerprint": args.fingerprint,
        "status": "completed" if code == 0 else "failed", "exit_code": code,
    }))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
