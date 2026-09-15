"""Scripted proposal used to exercise handoff; no model and no file modifications."""

from pathlib import Path

from research_intern.domain.research import CandidatePlan, IterationContext


class ScriptedProposer:
    backend = "simulated"

    async def run_iteration(self, context: IterationContext, working_directory: Path) -> CandidatePlan:
        latest = context.research_state.last_experiment
        parent = context.selected_parent
        observation = f"Selected {parent.experiment_id} with recorded score {parent.score}."
        if latest is not None:
            observation += f" Latest experiment {latest.experiment_id}: {latest.decision}."
        if latest is not None and latest.decision == "FAILED":
            diagnosis = f"Investigate the recorded {latest.failure_type}; no scientific score was established."
        elif latest is not None and latest.decision == "REJECT":
            diagnosis = "The latest tested intervention was rejected; start from the stronger recorded parent."
        else:
            diagnosis = "A recorded improvement is available; further improvement remains an untested hypothesis."
        return CandidatePlan(
            parent.experiment_id, observation, diagnosis,
            "Synthetic proposal: a different weight decay may improve the selected parent's validation score.",
            "Set fixture training weight decay to 0.005; avoid repeating the latest tested setting."
            if latest is not None and latest.decision in ("REJECT", "FAILED") else
            "Set fixture training weight decay to 0.02.",
            "A possible improvement under the existing constraints; the evaluator must measure it.",
        )
