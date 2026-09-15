"""Small typed inputs and decisions for one execution/evaluation slice."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


class SliceError(Exception):
    """An explicit failure at the execution-slice boundary."""


def is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Constraint:
    metric: str
    operator: Literal["min", "max"]
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.metric, str) or not self.metric.strip() or self.operator not in ("min", "max"):
            raise ValueError("A constraint needs a metric and min/max operator")
        if not is_finite_number(self.value):
            raise ValueError("Constraint thresholds must be finite numbers")


@dataclass(frozen=True)
class EvaluationRules:
    """The evaluation portion of a contract, not a full onboarding contract."""

    metric: str
    direction: Literal["maximize", "minimize"]
    target: float | None = None
    constraints: tuple[Constraint, ...] = ()
    version: str = "1.0"

    def __post_init__(self) -> None:
        if not isinstance(self.metric, str) or not self.metric.strip() or self.direction not in ("maximize", "minimize"):
            raise ValueError("An objective needs a metric and maximize/minimize direction")
        if self.version != "1.0":
            raise ValueError("Unsupported evaluation rules version")
        if self.target is not None and not is_finite_number(self.target):
            raise ValueError("The target must be a finite number")
        if not isinstance(self.constraints, tuple) or any(
            not isinstance(item, Constraint) for item in self.constraints
        ):
            raise ValueError("Constraints must be an immutable tuple of Constraint values")
        keys = [(item.metric, item.operator) for item in self.constraints]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate constraint operators for the same metric")
        for item in self.constraints:
            if item.operator == "min" and any(
                other.metric == item.metric and other.operator == "max"
                and other.value < item.value for other in self.constraints
            ):
                raise ValueError("A constraint minimum cannot exceed its maximum")


@dataclass(frozen=True)
class Candidate:
    """A prevalidated candidate handed off by the Git/validation slice."""

    parent_experiment: str
    git_commit: str
    hypothesis: str
    planned_intervention: str
    diff: str


@dataclass(frozen=True)
class JobRequest:
    experiment_id: str
    parent_experiment: str | None
    git_commit: str


@dataclass(frozen=True)
class CollectedResult:
    score: float
    metrics: dict[str, float]


@dataclass(frozen=True)
class Evaluation:
    score: float
    improved_over_parent: bool
    new_best: bool
    constraints_satisfied: bool
    constraint_results: tuple[dict, ...]
    goal_reached: bool
    decision: Literal["KEEP", "REJECT", "GOAL_REACHED"]
    conclusion: str


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    sequence: int
    parent_experiment: str | None
    parent_commit: str | None
    git_commit: str
    hypothesis: str
    planned_intervention: str
    diff: str
    backend: str
    state: str
    job_id: str | None
    job_status: str | None
    decision: str | None
    evaluation: Evaluation | None
    metrics: dict[str, float] | None
    failure_type: str | None
    failure_message: str | None
    outputs_path: str | None
    created_at: str
    updated_at: str

    @property
    def terminal(self) -> bool:
        return self.state in ("RECORDED", "FAILED")
