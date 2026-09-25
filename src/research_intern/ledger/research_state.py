"""Reconstruct compact research memory from one consistent ledger snapshot."""

from research_intern.domain.experiments import ExperimentRecord
from research_intern.domain.research import (
    ConstraintCheck, ExperimentBudget, ExperimentSummary, Finding, ResearchSnapshot, ResearchState,
    continuation_reasons,
)

SUMMARY_CHARACTERS = 600


def _compact(value: str) -> str:
    if len(value) <= SUMMARY_CHARACTERS:
        return value
    return value[:SUMMARY_CHARACTERS - 3] + "..."


def _summary(record: ExperimentRecord) -> ExperimentSummary:
    return ExperimentSummary(
        record.experiment_id, record.parent_experiment, record.git_commit,
        record.state, record.decision,
        record.evaluation.score if record.evaluation is not None else None,
        tuple(ConstraintCheck(**value) for value in record.evaluation.constraint_results)
        if record.evaluation is not None else (),
        _compact(record.hypothesis), _compact(record.planned_intervention),
        _compact(record.evaluation.conclusion) if record.evaluation is not None else None,
        _compact(record.failure_type) if record.failure_type is not None else None,
        _compact(record.failure_message) if record.failure_message is not None else None,
        record.job_id,
        record.outputs_path,
        f"experiments/{record.experiment_id}/diff.patch" if record.sequence > 0 and record.state != "PREPARED" else None,
    )


def build_research_state(snapshot: ResearchSnapshot, *, recent_limit: int = 5) -> ResearchState:
    """Keep exact decisions and attributable evidence; do not invent causal lessons."""
    if type(recent_limit) is not int or not 1 <= recent_limit <= 20:
        raise ValueError("recent_limit must be between 1 and 20")
    records = sorted(snapshot.experiments, key=lambda record: record.sequence)
    baseline = next((record for record in records if record.experiment_id == "EXP-000"), None)
    eligible = [record for record in records
                if record.state == "RECORDED" and record.decision in ("KEEP", "GOAL_REACHED")
                and record.evaluation is not None]
    # Eligible records are chronological, so a score tie retains the earlier best.
    choose = max if snapshot.rules.direction == "maximize" else min
    best = choose(eligible, key=lambda record: record.evaluation.score) if eligible else None
    completed = [record for record in records if record.sequence > 0 and record.terminal]

    def findings(decisions: tuple[str, ...]) -> tuple[Finding, ...]:
        matches = [record for record in completed if record.decision in decisions]
        return tuple(Finding(
            record.experiment_id, _compact(record.hypothesis),
            _compact(record.evaluation.conclusion) if record.evaluation is not None else
            f"{record.failure_type}: {_compact(record.failure_message or 'No failure detail recorded')}",
        ) for record in matches[-recent_limit:])

    latest = records[-1] if records else None
    questions = []
    if best is not None and best.sequence > 0:
        questions.append(f"Does the measured improvement in {best.experiment_id} reproduce?")
    if latest is not None and latest.decision == "FAILED":
        questions.append(f"What caused {latest.experiment_id} to fail ({latest.failure_type})?")
    elif latest is not None and latest.decision == "REJECT" and best is not None:
        questions.append(f"Which different intervention should be tested from {best.experiment_id} after {latest.experiment_id}?")
    allocated = sum(record.sequence > 0 for record in records)
    remaining = None if snapshot.max_experiments is None else max(snapshot.max_experiments - allocated, 0)
    reasons = continuation_reasons(snapshot)
    return ResearchState(
        "1.0", snapshot.mode, snapshot.revision, snapshot.rules,
        _summary(baseline) if baseline is not None else None,
        _summary(best) if best is not None else None,
        _summary(latest) if latest is not None else None,
        tuple(_summary(record) for record in completed[-recent_limit:]),
        findings(("KEEP", "GOAL_REACHED")), findings(("REJECT",)), findings(("FAILED",)),
        tuple(questions), ExperimentBudget(snapshot.max_experiments, allocated, remaining),
        not reasons, reasons,
    )
