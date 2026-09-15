"""Score structured evidence against fixed objective and constraint rules."""

from dataclasses import replace

from research_intern.domain.experiments import (
    CollectedResult, Evaluation, EvaluationRules, SliceError, is_finite_number,
)


class EvaluationError(SliceError):
    """Evidence cannot be evaluated under the recorded rules."""


def evaluate(
    rules: EvaluationRules, result: CollectedResult, *, parent_score: float, best_score: float,
) -> Evaluation:
    required = {rules.metric, *(item.metric for item in rules.constraints)}
    if not required.issubset(result.metrics) or not all(
        is_finite_number(value)
        for value in (result.score, parent_score, best_score, *result.metrics.values())
    ):
        raise EvaluationError("Evaluation requires finite objective and constraint metrics")
    if result.score != result.metrics[rules.metric]:
        raise EvaluationError("Primary score disagrees with the objective metric")

    def better(left: float, right: float) -> bool:
        return left > right if rules.direction == "maximize" else left < right

    checks = tuple({
        "metric": item.metric, "operator": item.operator, "threshold": item.value,
        "actual": result.metrics[item.metric],
        "satisfied": (result.metrics[item.metric] >= item.value if item.operator == "min"
                      else result.metrics[item.metric] <= item.value),
    } for item in rules.constraints)
    constraints_ok = all(item["satisfied"] for item in checks)
    improved = better(result.score, parent_score)
    new_best = constraints_ok and better(result.score, best_score)
    goal = constraints_ok and rules.target is not None and (
        result.score == rules.target or better(result.score, rules.target)
    )
    if not constraints_ok:
        decision = "REJECT"
        conclusion = "Hard constraints were violated; retain the current best."
    elif goal:
        decision = "GOAL_REACHED"
        conclusion = "The objective target and all hard constraints were satisfied."
    elif improved:
        decision = "KEEP"
        conclusion = "The objective improved over the selected parent with valid constraints."
    else:
        decision = "REJECT"
        conclusion = "The objective did not improve over the selected parent."
    return Evaluation(result.score, improved, new_best, constraints_ok, checks,
                      goal, decision, conclusion)


def evaluate_baseline(rules: EvaluationRules, result: CollectedResult) -> Evaluation:
    evaluated = evaluate(rules, result, parent_score=result.score, best_score=result.score)
    if not evaluated.constraints_satisfied:
        raise EvaluationError("The baseline must satisfy all hard constraints")
    return replace(evaluated, new_best=True,
                   decision="GOAL_REACHED" if evaluated.goal_reached else "KEEP",
                   conclusion="Imported the baseline as the initial best experiment.")
