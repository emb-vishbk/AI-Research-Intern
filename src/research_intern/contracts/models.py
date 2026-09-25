"""Validated contract values; no filesystem, SDK, or YAML dependency."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from research_intern.domain.experiments import Constraint, EvaluationRules, SliceError, is_finite_number
from research_intern.domain.research import validate_experiment_limit

CONTRACT_PATH = ".research_intern/contract.yaml"


class ContractError(SliceError):
    """The experiment's fixed research boundary is invalid."""


def relative_path(value: object, *, directory: bool = False) -> str:
    """Use literal portable paths, never globs, drives, traversal, or Git metadata."""
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ContractError("Contract paths must contain 1–512 characters")
    path = value[:-1] if directory and value.endswith("/") else value
    if any(c in value for c in "\\:*?[]\x00") or any(ord(c) < 32 for c in value):
        raise ContractError("Contract paths must be literal relative paths")
    for part in path.split("/"):
        if (not part or part in (".", "..") or part.endswith((".", " "))
                or part.casefold() == ".git"):
            raise ContractError("Contract paths cannot traverse directories or name Git metadata")
    return path + "/" if directory else path


def covers(scope: str, path: str) -> bool:
    """Conservative case-insensitive comparison also works on Windows volumes."""
    scope, path = scope.casefold(), path.rstrip("/").casefold()
    return path == scope.rstrip("/") or (scope.endswith("/") and path.startswith(scope))


def overlaps(left: str, right: str) -> bool:
    return covers(left, right) or covers(right, left)


def mapping(value: object, name: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContractError(f"{name} must be an object with string keys")
    if required - value.keys():
        raise ContractError(f"{name} is missing: {', '.join(sorted(required - value.keys()))}")
    if value.keys() - required - optional:
        raise ContractError(f"{name} has unsupported fields: {', '.join(sorted(value.keys() - required - optional))}")
    return value


def metric_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 128:
        raise ContractError("Metric names must contain 1–128 non-blank characters")
    if any(ord(c) < 32 for c in value):
        raise ContractError("Metric names cannot contain control characters")
    return value


@dataclass(frozen=True)
class OutputPaths:
    root: str = "experiment_outputs/"
    run: str = "run.json"
    metrics: str = "metrics.json"
    history: str = "metrics_history.json"
    logs: str = "logs/"
    artifacts: str = "artifacts/"

    def __post_init__(self) -> None:
        for name in ("root", "run", "metrics", "history", "logs", "artifacts"):
            object.__setattr__(self, name, relative_path(getattr(self, name), directory=name in ("root", "logs", "artifacts")))
        paths = (self.run, self.metrics, self.history, self.logs, self.artifacts)
        for i, path in enumerate(paths):
            for other in paths[i + 1:]:
                # A file must not also be an ancestor of another output.
                if overlaps(path.rstrip("/") + "/", other.rstrip("/") + "/"):
                    raise ContractError("Output files and directories must have distinct, non-overlapping paths")


@dataclass(frozen=True)
class ExecutionConfig:
    backend: str
    job_config: str
    native: bool = False


@dataclass(frozen=True)
class Budget:
    max_experiments: int
    max_gpu_hours: float | None = None
    max_ai_credits: float | None = None


@dataclass(frozen=True)
class Scope:
    editable: tuple[str, ...]
    protected: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationProtocol:
    """Researcher-defined measurement procedure and pinned local inputs."""

    metric_definition: str
    procedure: str
    dataset_version: str
    evaluator: str
    evaluator_sha256: str
    validation_split: str
    validation_split_sha256: str

    @classmethod
    def from_dict(cls, value: object) -> EvaluationProtocol:
        data = mapping(value, "evaluation", set(cls.__dataclass_fields__))
        for name in ("metric_definition", "procedure", "dataset_version"):
            text = data[name]
            limit = 512 if name == "dataset_version" else 4000
            if (not isinstance(text, str) or not text.strip() or len(text) > limit
                    or any(ord(c) < 32 and c not in "\n\t" for c in text)):
                raise ContractError(f"evaluation.{name} needs non-blank text of at most {limit} characters")
        for name in ("evaluator", "validation_split"):
            relative_path(data[name])
        for name in ("evaluator_sha256", "validation_split_sha256"):
            if not isinstance(data[name], str) or not re.fullmatch(r"[a-f0-9]{64}", data[name]):
                raise ContractError(f"evaluation.{name} must be a lowercase SHA-256 digest")
        return cls(**data)


@dataclass(frozen=True)
class ExperimentContract:
    rules: EvaluationRules
    execution: ExecutionConfig
    outputs: OutputPaths
    scope: Scope
    budget: Budget
    version: str = "1.0"
    evaluation: EvaluationProtocol | None = None
    goal: str = ""

    @property
    def protected_paths(self) -> tuple[str, ...]:
        # Policy, infrastructure configuration, and generated scores cannot be edited.
        evaluation_paths = () if self.evaluation is None else (self.evaluation.evaluator, self.evaluation.validation_split)
        return (*self.scope.protected, ".research_intern/", self.execution.job_config, self.outputs.root, *evaluation_paths)

    @property
    def evaluation_fingerprint(self) -> str | None:
        if self.evaluation is None:
            return None
        contract = self.to_dict()
        policy = {key: contract[key] for key in ("objective", "constraints", "evaluation")}
        raw = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def allows(self, path: str) -> bool:
        if any(part.casefold() in (".git", ".gitignore", ".gitattributes", ".gitmodules")
               for part in path.rstrip("/").split("/")):
            return False
        return (any(covers(scope, path) for scope in self.scope.editable)
                and not any(covers(scope, path) for scope in self.protected_paths))

    def to_dict(self) -> dict:
        objective = {"metric": self.rules.metric, "direction": self.rules.direction}
        if self.rules.target is not None:
            objective["target"] = self.rules.target
        constraints: dict[str, dict] = {}
        for item in self.rules.constraints:
            constraints.setdefault(item.metric, {})[item.operator] = item.value
        result = {
            "version": self.version, "objective": objective,
            "execution": asdict(self.execution), "outputs": asdict(self.outputs),
            "scope": {"editable": list(self.scope.editable), "protected": list(self.scope.protected)},
            "constraints": constraints,
            "budget": {key: value for key, value in asdict(self.budget).items() if value is not None},
        }
        if not self.execution.native:
            result["execution"].pop("native", None)
        if self.goal:
            result["goal"] = self.goal
        if self.evaluation is not None:
            result["evaluation"] = asdict(self.evaluation)
        return result

    @classmethod
    def from_dict(cls, value: object) -> ExperimentContract:
        data = mapping(value, "contract", {"version", "objective", "execution", "outputs", "scope", "budget"}, {"constraints", "evaluation", "goal"})
        if not isinstance(data.get("goal", ""), str) or len(data.get("goal", "")) > 4000:
            raise ContractError("The research goal must be text of at most 4,000 characters")
        if data["version"] != "1.0":
            raise ContractError('Only contract version "1.0" is supported')
        objective = mapping(data["objective"], "objective", {"metric", "direction"}, {"target"})
        metric_name(objective["metric"])
        if "target" in objective and not is_finite_number(objective["target"]):
            raise ContractError("The objective target must be a finite number")
        constraints = mapping(data.get("constraints", {}), "constraints", set(), set(data.get("constraints", {}))
                              if isinstance(data.get("constraints", {}), dict) else set())
        checks = []
        for name, bounds in sorted(constraints.items()):
            metric_name(name)
            bounds = mapping(bounds, f"constraint {name}", set(), {"min", "max"})
            if not bounds:
                raise ContractError("Constraints need a min or max bound")
            for operator, threshold in sorted(bounds.items()):
                try:
                    checks.append(Constraint(name, operator, threshold))
                except ValueError as exc:
                    raise ContractError(str(exc)) from exc
            if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
                raise ContractError(f"Constraint {name} has a minimum greater than its maximum")
        try:
            rules = EvaluationRules(objective["metric"], objective["direction"], objective.get("target"), tuple(checks))
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        execution = mapping(data["execution"], "execution", {"backend", "job_config"}, {"native"})
        if execution["backend"] != "azure_ml":
            raise ContractError("The experiment contract supports only azure_ml; simulation is an application mode")
        if type(execution.get("native", False)) is not bool:
            raise ContractError("execution.native must be a boolean")
        execution = ExecutionConfig("azure_ml", relative_path(execution["job_config"]), execution.get("native", False))
        outputs = mapping(data["outputs"], "outputs", {"root"}, {"run", "metrics", "history", "logs", "artifacts"})
        outputs = OutputPaths(**outputs)
        budget = mapping(data["budget"], "budget", {"max_experiments"}, {"max_gpu_hours", "max_ai_credits"})
        try:
            validate_experiment_limit(budget["max_experiments"])
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        for key in ("max_gpu_hours", "max_ai_credits"):
            if key in budget and (not is_finite_number(budget[key]) or budget[key] < 0):
                raise ContractError(f"{key} must be a finite non-negative number")
        scope = mapping(data["scope"], "scope", {"editable", "protected"})
        paths = {}
        for name in ("editable", "protected"):
            if not isinstance(scope[name], list) or (name == "editable" and not scope[name]):
                raise ContractError("Scope must contain path lists and at least one editable path")
            paths[name] = tuple(relative_path(p, directory=isinstance(p, str) and p.endswith("/")) for p in scope[name])
            if len({p.casefold() for p in paths[name]}) != len(paths[name]):
                raise ContractError("Scope paths cannot contain duplicates")
        evaluation = EvaluationProtocol.from_dict(data["evaluation"]) if "evaluation" in data else None
        contract = cls(rules, execution, outputs, Scope(**paths), Budget(**budget), evaluation=evaluation, goal=data.get("goal", ""))
        for editable in contract.scope.editable:
            if not contract.allows(editable) or any(overlaps(editable, p) for p in contract.protected_paths):
                raise ContractError("Editable paths overlap protected policy, execution, outputs, or researcher scope")
        evaluation_paths = () if evaluation is None else (evaluation.evaluator, evaluation.validation_split)
        for protected in (*contract.scope.protected, ".research_intern/", execution.job_config, *evaluation_paths):
            if overlaps(outputs.root, protected):
                raise ContractError("The output root must not overlap protected inputs")
        return contract
