"""Prepare one fresh proposal from persisted evidence and controller-owned limits."""

from collections.abc import Callable
from pathlib import Path

from research_intern.copilot.proposer import Proposer
from research_intern.domain.experiments import SliceError
from research_intern.domain.research import CandidatePlan, IterationContext, ResearchHandoff, ResearchState
from research_intern.ledger.research_state import build_research_state
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.paths import child_path


class HandoffBlocked(SliceError):
    """The research run is stopped, incomplete, or changed during proposal generation."""


def build_iteration_context(state: ResearchState) -> IterationContext:
    if not state.continuation_allowed:
        raise HandoffBlocked("Cannot start a research proposal: " + ", ".join(state.blocking_reasons))
    if state.best_experiment is None:
        raise HandoffBlocked("The next iteration needs an eligible parent")
    return IterationContext(
        state, state.best_experiment,
        "Propose one intervention from the selected parent using the recorded objective, "
        "constraints, and evidence. Treat hypotheses and recorded outcomes as evidence, not "
        "proof of a causal mechanism. Retain rejected/failed findings when choosing a direction. "
        "Return a concise observation, tentative diagnosis, hypothesis, intervention, and expected effect. "
        "Do not assign experiment IDs, change limits, decide success, or execute work. "
        "This handoff contains evaluation rules only; code changes require the future full "
        "contract and Git/permission validation stage.",
    )


class HandoffController:
    def __init__(self, ledger: Ledger, proposer_factory: Callable[[], Proposer]):
        self.ledger = ledger
        self.proposer_factory = proposer_factory

    async def propose_next(self, working_directory: Path) -> ResearchHandoff:
        state = build_research_state(self.ledger.snapshot())
        context = build_iteration_context(state)
        # The current integration is limited to a fixture within this simulation.
        # Future research-repository access belongs to the contract/workspace layer.
        try:
            relative = working_directory.absolute().relative_to(self.ledger.root)
        except ValueError as exc:
            raise HandoffBlocked("The proposal fixture must remain inside this simulation") from exc
        directory = child_path(self.ledger.root, str(relative))
        if not directory.is_dir():
            raise HandoffBlocked("The proposal fixture directory is missing")
        proposer = self.proposer_factory()
        if proposer.backend != "simulated":
            raise HandoffBlocked("This handoff slice does not enable live proposers")
        plan = await proposer.run_iteration(context, directory)
        if build_research_state(self.ledger.snapshot()) != state:
            raise HandoffBlocked("Research history or stopping state changed; discard this stale proposal")
        if not isinstance(plan, CandidatePlan) or plan.parent_experiment != context.selected_parent.experiment_id:
            raise HandoffBlocked("The proposal must identify the controller-selected parent")
        return ResearchHandoff(context, plan)
