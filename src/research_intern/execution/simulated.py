"""Persistent canned jobs for offline development. No experiment code is run."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from research_intern.contracts.models import OutputPaths

from research_intern.domain.experiments import EvaluationRules, JobRequest
from research_intern.execution.adapter import ExecutionError, JobStatus, SubmissionError
from research_intern.execution.outputs import read_json
from research_intern.workspace.paths import child_path

SCENARIOS = ("improve", "regress", "constraint", "goal", "runtime-failure", "invalid-output",
             "submission-failure")


def write_outputs(
    destination: Path, rules: EvaluationRules, request: JobRequest, *,
    score: float = 0.84, latency_ms: float = 40.0, failed: bool = False,
    invalid: bool = False, paths: OutputPaths = OutputPaths(),
) -> None:
    """Write explicitly synthetic standard outputs into a new directory."""
    destination.mkdir(exist_ok=False)
    (destination / paths.logs).mkdir(parents=True)
    (destination / paths.artifacts).mkdir(parents=True)
    timestamp = datetime.now(UTC).isoformat()
    run = {"schema_version": "1.0", "status": "failed" if failed else "completed",
           "experiment_id": request.experiment_id, "parent_experiment": request.parent_experiment,
           "started_at": timestamp, "completed_at": timestamp, "parameters": {},
           "simulation": True}
    if failed:
        run.update(failure_type="simulated_training_error", exit_code=1)
    payloads = {paths.run: run}
    if not failed:
        payloads[paths.history] = {rules.metric: [score]}
        if not invalid:
            payloads[paths.metrics] = {
                "schema_version": "1.0", "simulation": True,
                "primary_metric": {"name": rules.metric, "direction": rules.direction, "value": score},
                "metrics": {rules.metric: score, "latency_ms": latency_ms},
            }
    for name, payload in payloads.items():
        (destination / name).parent.mkdir(parents=True, exist_ok=True)
        (destination / name).write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n",
                                       encoding="utf-8")
    (destination / paths.logs / "simulation.log").write_text(
        "SIMULATED output. No training code, model session, or Azure job was executed.\n",
        encoding="utf-8",
    )


class SimulatedExecutor:
    backend = "simulated"

    def __init__(self, root: Path, rules: EvaluationRules, scenario: str = "improve", *,
                 paths: OutputPaths = OutputPaths(), scenarios: tuple[str, ...] | None = None):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown simulation scenario")
        self.root = root.resolve(strict=True)
        self.rules = rules
        self.scenario = scenario
        if scenarios is not None and (not scenarios or any(value not in SCENARIOS for value in scenarios)):
            raise ValueError("Unknown simulation scenario sequence")
        self.scenarios = scenarios
        self.output_paths = paths
        self.jobs = child_path(self.root, "simulated_jobs")
        self.jobs.mkdir(exist_ok=True)

    def _path(self, job_id: str) -> Path:
        if not re.fullmatch(r"simulated-EXP-[0-9]{3,}", job_id):
            raise ExecutionError("Unknown simulated job ID")
        return child_path(self.jobs, f"{job_id}.json")

    def _save(self, path: Path, job: dict) -> None:
        # Atomic replacement keeps the simulated status readable after interruption.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.jobs,
                                         prefix="job-", suffix=".tmp", delete=False) as stream:
            json.dump(job, stream, allow_nan=False)
            temporary = Path(stream.name)
        temporary.replace(path)

    def submit_job(self, request: JobRequest) -> str:
        scenario = self.scenario if self.scenarios is None else self.scenarios[(int(request.experiment_id[4:]) - 1) % len(self.scenarios)]
        if scenario == "submission-failure":
            raise SubmissionError("Simulated submission rejection; no job was created")
        job_id = f"simulated-{request.experiment_id}"
        path = self._path(job_id)
        if path.exists():
            raise ExecutionError("A simulated job already exists; reconcile instead of resubmitting")
        self._save(path, {"request": asdict(request), "scenario": scenario,
                          "polls": 0, "status": "queued", "simulation": True,
                          "output_paths": asdict(self.output_paths)})
        return job_id

    def find_job(self, request: JobRequest) -> str | None:
        """Reconcile an interrupted submission against the simulator's durable job."""
        job_id = f"simulated-{request.experiment_id}"
        path = self._path(job_id)
        if not path.exists():
            return None
        job = read_json(path)
        if (job.get("simulation") is not True or job.get("request") != asdict(request)
                or job.get("scenario") not in SCENARIOS
                or job.get("status") not in ("queued", "running", "completed", "failed", "cancelled")
                or OutputPaths(**job.get("output_paths", {})) != self.output_paths):
            raise ExecutionError("The existing simulated job does not match this candidate; reconciliation is blocked")
        return job_id

    def get_status(self, job_id: str) -> JobStatus:
        path = self._path(job_id)
        job = read_json(path)
        if job["status"] not in ("completed", "failed", "cancelled"):
            job["polls"] += 1
            job["status"] = "running" if job["polls"] == 1 else (
                "failed" if job["scenario"] == "runtime-failure" else "completed"
            )
            self._save(path, job)
        return job["status"]

    def download_outputs(self, job_id: str, destination: Path) -> None:
        job = read_json(self._path(job_id))
        if job["status"] not in ("completed", "failed", "cancelled"):
            raise ExecutionError("Cannot collect a simulated job before it finishes")
        try:
            destination = child_path(self.root, str(destination.relative_to(self.root)))
        except ValueError as exc:
            raise ExecutionError("Simulated outputs must remain inside their run directory") from exc
        if destination.exists():
            raise ExecutionError("Refusing to overwrite existing experiment outputs")
        scenario = job["scenario"]
        score = {"improve": 0.84, "regress": 0.75, "constraint": 0.90, "goal": 0.90}.get(scenario, 0.84)
        # An interrupted collection leaves its partial evidence alongside the record;
        # only a complete directory is published as experiment_outputs.
        staging = Path(tempfile.mkdtemp(prefix="collection-", dir=destination.parent))
        staged_outputs = staging / "experiment_outputs"
        write_outputs(staged_outputs, self.rules, JobRequest(**job["request"]), score=score,
                      latency_ms=60.0 if scenario == "constraint" else 40.0,
                      failed=job["status"] in ("failed", "cancelled"),
                      invalid=scenario == "invalid-output", paths=OutputPaths(**job.get("output_paths", {})))
        staged_outputs.rename(destination)
        staging.rmdir()

    def cancel_job(self, job_id: str) -> None:
        path = self._path(job_id)
        job = read_json(path)
        if job["status"] not in ("completed", "failed", "cancelled"):
            job["status"] = "cancelled"
            self._save(path, job)
