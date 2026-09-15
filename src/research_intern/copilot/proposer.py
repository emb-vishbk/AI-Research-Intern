"""Proposal seam for a fresh research turn; no SDK imports or execution authority."""

from pathlib import Path
from typing import Protocol

from research_intern.domain.research import CandidatePlan, IterationContext


class Proposer(Protocol):
    backend: str

    async def run_iteration(self, context: IterationContext, working_directory: Path) -> CandidatePlan:
        """Return a concise plan. A live adapter will own its entire session lifecycle."""
        ...
