"""Run a fixed researcher-reviewed scorer outside the editable training process.

The scorer owns metric computation; training's self-reported scores are never used
for live decisions. Its Python environment and data access are researcher-provided.
This is process separation, not an OS sandbox for malicious scorer implementations.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from research_intern.contracts.models import covers, relative_path
from research_intern.domain.experiments import CollectedResult, JobRequest, is_finite_number
from research_intern.execution.outputs import OutputError, read_json, validate_tree
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inventory(root: Path) -> dict[str, str]:
    validate_tree(root)
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*")) if p.is_file()}


def freeze_scorer(repository: Path, target: Path, contract, paths: list[str]) -> dict:
    if contract.evaluation is None:
        raise OutputError("A pinned evaluation protocol is required")
    paths = sorted(set(relative_path(p) for p in paths))
    if not {contract.evaluation.evaluator, contract.evaluation.validation_split}.issubset(paths):
        raise OutputError("Scorer bundle must include the pinned evaluator and split")
    checked = {}
    for name in paths:
        if not any(covers(p, name) for p in contract.protected_paths) or contract.allows(name):
            raise OutputError("Only protected files may enter the trusted scorer bundle")
        source = child_path(repository, name)
        if not source.is_file() or source.stat().st_nlink != 1 or source.stat().st_size > 16 * 1024 * 1024:
            raise OutputError("Scorer inputs must be bounded regular files")
        checked[name] = source.read_bytes()
    if sum(map(len, checked.values())) > 64 * 1024 * 1024:
        raise OutputError("Scorer bundle exceeds 64 MiB")
    expected = {name: hashlib.sha256(payload).hexdigest() for name, payload in checked.items()}
    if target.exists():
        if inventory(target) != expected:
            raise OutputError("A frozen scorer cannot be overwritten")
        return expected  # Recover publication before live.json was committed.
    with tempfile.TemporaryDirectory(dir=target.parent, prefix="freeze-") as temporary:
        stage = Path(temporary) / "bundle"
        for name, payload in checked.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        stage.rename(target)
    return inventory(target)


class TrustedScorer:
    def __init__(self, root: Path, contract, *, python: str, files: dict[str, str], timeout_seconds: int):
        self.root, self.contract = root, contract
        self.code = child_path(root, "trusted-scorer")
        self.python = str(Path(python).absolute())
        self.files, self.timeout = files, timeout_seconds
        if not Path(self.python).is_file() or type(self.timeout) is not int or not 1 <= self.timeout <= 3600:
            raise OutputError("Choose an installed scoring Python and a timeout from 1 to 3600 seconds")
        self.check()

    def check(self):
        if inventory(self.code) != self.files:
            raise OutputError("Frozen scoring code or reference inputs changed")

    def verify(self, outputs: Path, request: JobRequest, job_id: str) -> CollectedResult:
        self.check()
        run = read_json(outputs / self.contract.outputs.run)
        if run.get("source_commit") != request.git_commit:
            raise OutputError("Workload output lacks the submitted source_commit")
        identity = {**asdict(request), "job_id": job_id,
                    "evaluation_fingerprint": self.contract.evaluation_fingerprint}
        experiment = child_path(self.root, "experiments", request.experiment_id)
        receipt_path = experiment / "verified-score.json"
        evidence = inventory(outputs)
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            if receipt.get("identity") != identity or receipt.get("artifacts") != evidence or receipt.get("scorer") != self.files:
                raise OutputError("Previously verified evidence changed")
            return self._result(receipt["metrics"])
        with tempfile.TemporaryDirectory(dir=self.root, prefix="scoring-") as temporary:
            work = Path(temporary)
            request_file, result_file = work / "request.json", work / "score.json"
            request_file.write_text(json.dumps({**identity, "objective": self.contract.to_dict()["objective"],
                                               "constraints": self.contract.to_dict()["constraints"]}), encoding="utf-8")
            # -I excludes candidate cwd, PYTHONPATH and user site packages. Only the
            # reviewed frozen module tree is inserted, never the candidate source.
            driver = "import runpy,sys; code,entry=sys.argv[1:3]; sys.path.insert(0,code); sys.argv=sys.argv[2:]; runpy.run_path(entry,run_name='__main__')"
            command = [self.python, "-I", "-B", "-c", driver, str(self.code),
                       str(self.code / self.contract.evaluation.evaluator), "--outputs", str(outputs),
                       "--request", str(request_file), "--result", str(result_file)]
            environment = {k: v for k, v in os.environ.items() if k.upper() in
                           {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"}}
            environment.update(TMPDIR=str(work), TEMP=str(work), TMP=str(work))
            try:
                with (work / "scorer.log").open("wb") as log:
                    subprocess.run(command, cwd=work, env=environment, stdout=log, stderr=log,
                                   check=True, timeout=self.timeout)
                payload = read_json(result_file)
                if payload.get("identity") != identity:
                    raise OutputError("Independent scorer returned the wrong experiment identity")
                result = self._result(payload.get("metrics"))
            except (subprocess.SubprocessError, OSError) as exc:
                raise OutputError(f"Independent scoring failed ({type(exc).__name__})") from exc
            finally:
                log_path = work / "scorer.log"
                if log_path.exists():
                    with log_path.open("rb") as log:
                        atomic_bytes(experiment / "scorer.log", log.read(1024 * 1024))
        self.check()
        if inventory(outputs) != evidence:
            raise OutputError("Training evidence changed during scoring")
        atomic_bytes(receipt_path, json.dumps({"identity": identity, "artifacts": evidence,
                                              "scorer": self.files, "metrics": result.metrics}, allow_nan=False).encode())
        return result

    def _result(self, metrics) -> CollectedResult:
        required = {self.contract.rules.metric, *(c.metric for c in self.contract.rules.constraints)}
        if not isinstance(metrics, dict) or not required.issubset(metrics) or not all(is_finite_number(v) for v in metrics.values()):
            raise OutputError("Independent scorer must supply every finite objective/constraint metric")
        return CollectedResult(float(metrics[self.contract.rules.metric]), {k: float(v) for k,v in metrics.items()})
