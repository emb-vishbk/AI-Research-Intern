"""Validate standardized outputs without interpreting logs or judging quality."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from research_intern.contracts.models import OutputPaths

from research_intern.domain.experiments import (
    CollectedResult, EvaluationRules, JobRequest, SliceError, is_finite_number,
)

MAX_JSON_BYTES = 4 * 1024 * 1024


class OutputError(SliceError):
    """The standardized output contract was not satisfied."""


class RunFailedError(SliceError):
    """The workload reported execution failure in run.json."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-finite JSON constant")


def read_json(path: Path) -> dict:
    try:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise OutputError(f"{path.name} must be a regular file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise OutputError(f"{path.name} exceeds the 4 MiB JSON limit")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise OutputError(f"{path.name} is missing or is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise OutputError(f"{path.name} must contain a JSON object")
    return value


def validate_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise OutputError("Expected a regular experiment_outputs directory")
    try:
        def walk_error(error: OSError) -> None:
            raise error

        for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            for name in dirs + files:
                mode = (Path(directory) / name).lstat().st_mode
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise OutputError("Output trees may contain only regular files/directories")
    except OSError as exc:
        raise OutputError("Unable to inspect the output tree") from exc


def collect_outputs(root: Path, rules: EvaluationRules, request: JobRequest,
                    paths: OutputPaths = OutputPaths()) -> CollectedResult:
    validate_tree(root)
    run = read_json(root / paths.run)
    if run.get("schema_version") != "1.0":
        raise OutputError("Unsupported run.json schema_version")
    # Failure files may omit identity, but must never claim another experiment.
    for key, expected in (("experiment_id", request.experiment_id),
                          ("parent_experiment", request.parent_experiment)):
        if key in run and run[key] != expected:
            raise OutputError(f"run.json {key} does not match the submitted experiment")
    if run.get("status") == "failed":
        raise RunFailedError("The workload reported failure in run.json")
    if run.get("status") != "completed":
        raise OutputError("run.json status must be completed or failed")
    if "experiment_id" not in run or "parent_experiment" not in run:
        raise OutputError("Completed run.json must identify the experiment and its parent")
    if "exit_code" in run and (type(run["exit_code"]) is not int or run["exit_code"] != 0):
        raise OutputError("A completed run cannot report an unsuccessful exit_code")
    for directory in (paths.logs, paths.artifacts):
        if not (root / directory).is_dir():
            raise OutputError(f"Required {directory}/ directory is missing")
    history = read_json(root / paths.history)
    if any(not isinstance(values, list) or not all(is_finite_number(v) for v in values)
           for values in history.values()):
        raise OutputError("Metric history must contain arrays of finite numbers")
    payload = read_json(root / paths.metrics)
    if payload.get("schema_version") != "1.0":
        raise OutputError("Unsupported metrics.json schema_version")
    primary, metrics = payload.get("primary_metric"), payload.get("metrics")
    if not isinstance(primary, dict) or not isinstance(metrics, dict):
        raise OutputError("metrics.json needs primary_metric and metrics objects")
    if primary.get("name") != rules.metric or primary.get("direction") != rules.direction:
        raise OutputError("The primary metric name/direction disagrees with the objective")
    score = primary.get("value")
    if not is_finite_number(score) or not all(is_finite_number(v) for v in metrics.values()):
        raise OutputError("Final metrics must be finite numbers, never booleans")
    required = {rules.metric, *(item.metric for item in rules.constraints)}
    if not required.issubset(metrics) or metrics[rules.metric] != score:
        raise OutputError("Objective/constraint metrics are missing or the primary value disagrees")
    return CollectedResult(float(score), {key: float(value) for key, value in metrics.items()})
