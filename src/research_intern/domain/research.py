"""Persisted-history views and proposal handoff values, independent of adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from research_intern.contracts.models import ExperimentContract

from research_intern.domain.experiments import EvaluationRules, ExperimentRecord


def validate_experiment_limit(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("max_experiments must be a non-negative integer")


@dataclass(frozen=True)
class ResearchSnapshot:
    """A consistent ledger read; source records remain the permanent memory."""

    rules: EvaluationRules
    experiments: tuple[ExperimentRecord, ...]
    max_experiments: int | None
    stop_requested: bool
    revision: int
    mode: str = "simulated"


def continuation_reasons(snapshot: ResearchSnapshot) -> tuple[str, ...]:
    """Reasons to prevent a NEW proposal/reservation, not to stop polling a job."""
    reasons = []
    if not any(record.experiment_id == "EXP-000" and record.state == "RECORDED"
               and record.decision in ("KEEP", "GOAL_REACHED") for record in snapshot.experiments):
        reasons.append("BASELINE_MISSING")
    if snapshot.stop_requested:
        reasons.append("HUMAN_STOP")
    if any(record.decision == "GOAL_REACHED" for record in snapshot.experiments):
        reasons.append("GOAL_REACHED")
    if any(not record.terminal for record in snapshot.experiments):
        reasons.append("EXPERIMENT_IN_PROGRESS")
    if snapshot.max_experiments is None:
        reasons.append("BUDGET_NOT_CONFIGURED")
    elif sum(record.sequence > 0 for record in snapshot.experiments) >= snapshot.max_experiments:
        reasons.append("EXPERIMENT_BUDGET_EXHAUSTED")
    return tuple(reasons)


@dataclass(frozen=True)
class ExperimentBudget:
    max_experiments: int | None
    allocated_experiments: int
    remaining_experiments: int | None
    # Unknown until those integrations provide authoritative limits and usage.
    remaining_gpu_hours: float | None = None
    remaining_copilot_credits: float | None = None


@dataclass(frozen=True)
class ConstraintCheck:
    metric: str
    operator: str
    threshold: float
    actual: float
    satisfied: bool


@dataclass(frozen=True)
class ExperimentSummary:
    experiment_id: str
    parent_experiment: str | None
    git_commit: str
    state: str
    decision: str | None
    score: float | None
    constraint_results: tuple[ConstraintCheck, ...]
    hypothesis: str
    planned_intervention: str
    conclusion: str | None
    failure_type: str | None
    failure_message: str | None
    job_id: str | None
    outputs_path: str | None
    diff_path: str | None


@dataclass(frozen=True)
class Finding:
    experiment_id: str
    hypothesis: str
    observation: str


@dataclass(frozen=True)
class ResearchState:
    schema_version: str
    mode: str
    ledger_revision: int
    objective: EvaluationRules
    baseline: ExperimentSummary | None
    best_experiment: ExperimentSummary | None
    last_experiment: ExperimentSummary | None
    recent_results: tuple[ExperimentSummary, ...]
    supported_directions: tuple[Finding, ...]
    rejected_directions: tuple[Finding, ...]
    known_failures: tuple[Finding, ...]
    open_questions: tuple[str, ...]
    budget: ExperimentBudget
    continuation_allowed: bool
    blocking_reasons: tuple[str, ...]


@dataclass(frozen=True)
class IterationContext:
    research_state: ResearchState
    selected_parent: ExperimentSummary
    task: str
    contract: ExperimentContract | None = None
    attempt_number: int = 0
    preparation_failures: tuple[dict, ...] = ()


@dataclass(frozen=True)
class CandidatePlan:
    """A concise proposed intervention, not a committed or evaluated candidate."""

    parent_experiment: str
    observation: str
    diagnosis: str
    hypothesis: str
    planned_intervention: str
    expected_effect: str

    def __post_init__(self) -> None:
        for value in (self.parent_experiment, self.observation, self.diagnosis,
                      self.hypothesis, self.planned_intervention, self.expected_effect):
            if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ValueError("Candidate plan fields must contain 1–2000 characters")


@dataclass(frozen=True)
class ResearchHandoff:
    context: IterationContext
    plan: CandidatePlan
