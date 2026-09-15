"""A fixture-only editing proposer for testing the candidate boundary."""

from pathlib import Path

from research_intern.domain.research import CandidatePlan, IterationContext


class ScriptedEditor:
    backend = "simulated"

    def __init__(self, value: float = 0.01, *, path: str = "labeller.py"):
        self.value = value
        self.path = path

    async def run_iteration(self, context: IterationContext, working_directory: Path) -> CandidatePlan:
        # This deliberately includes an invalid-edit scenario so tests can prove
        # that the controller checks real changes instead of trusting a summary.
        (working_directory / self.path).write_text(
            f"# Synthetic fixture; no ML code executes in this demo.\nWEIGHT_DECAY = {self.value}\n",
            encoding="utf-8",
        )
        return CandidatePlan(
            context.selected_parent.experiment_id,
            f"Selected {context.selected_parent.experiment_id} with recorded score {context.selected_parent.score}.",
            "A different regularization setting is an untested possibility.",
            "Synthetic hypothesis: changing regularization may improve the objective.",
            f"Set {self.path} weight decay to {self.value}.",
            "Any improvement must be established by the external evaluator.",
        )


class LoopEditor:
    """Fixture interventions exercise retries; the controller never assigns scores."""
    backend = "simulated"

    def __init__(self, scenario: str = "mixed"):
        self.scenario = scenario

    async def run_iteration(self, context: IterationContext, working_directory: Path) -> CandidatePlan:
        editor = ScriptedEditor(context.attempt_number * 0.01,
                                path="evaluate.py" if self.scenario == "protected-first" and context.attempt_number == 1 else "labeller.py")
        plan = await editor.run_iteration(context, working_directory)
        if self.scenario == "invalid-always" or (self.scenario == "invalid-first" and context.attempt_number == 1):
            (working_directory / "labeller.py").write_text("def invalid_fixture(:\n", encoding="utf-8")
        return plan
