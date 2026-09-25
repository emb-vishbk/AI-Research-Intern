"""Run all offline tests against an exact source copy on the native temp filesystem.

This avoids slow Git fixture operations on Windows-mounted WSL volumes. No service
authentication or live calls. Results are also saved under .runtime/test-results.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
logs = root / ".runtime/test-results"
logs.mkdir(parents=True, exist_ok=True)
files = [p for directory in ("src", "tests") for p in (root / directory).rglob("*.py")]
for path in files:
    ast.parse(path.read_bytes(), filename=str(path))
for path in (root / "schemas").glob("*.json"):
    json.loads(path.read_bytes())
print(f"Parsed {len(files)} Python files and the JSON schemas.", flush=True)
with tempfile.TemporaryDirectory(prefix="research-intern-check-") as temporary:
    stage = Path(temporary)
    for name in ("src", "tests", "schemas", "Docs"):
        shutil.copytree(root / name, stage / name, ignore=shutil.ignore_patterns("__pycache__"))
    for path in root.glob("*"):
        if path.is_file():
            shutil.copy2(path, stage / path.name)
    manifest = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in stage.rglob("*") if p.is_file()}
    (logs / "source.json").write_text(json.dumps(manifest, indent=2))
    environment = dict(os.environ, PYTHONPATH=str(stage / "src") + os.pathsep + str(stage / "tests"),
                       PYTHONDONTWRITEBYTECODE="1")
    with (logs / "tests.log").open("w") as output:
        selection = sys.argv[1:] or ["discover", "-s", "tests", "-v"]
        process = subprocess.Popen([sys.executable, "-B", "-m", "unittest", *selection],
                                   cwd=stage, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            output.write(line)
            output.flush()
            print(line, end="", flush=True)
        result = process.wait()
    print(f"Offline test exit code: {result}. Log: {logs / 'tests.log'}", flush=True)
    sys.exit(result)
