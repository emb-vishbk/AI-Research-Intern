"""Compose execution, evidence, stopping, and fresh scripted proposals offline."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from research_intern.controller.execution import ExecutionController
from research_intern.controller.handoff import HandoffController
from research_intern.copilot.simulated import ScriptedProposer
from research_intern.domain.experiments import Candidate, Constraint, EvaluationRules, JobRequest, SliceError
from research_intern.domain.research import ResearchState, validate_experiment_limit
from research_intern.execution.simulated import SimulatedExecutor, write_outputs
from research_intern.ledger.research_state import build_research_state
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.fixture import GitFixture
from research_intern.workspace.paths import child_path


async def run_handoff_demo(
    workspace: Path, *, outcome: str = "regress", max_experiments: int = 3,
) -> tuple[Path, ResearchState]:
    if outcome not in ("regress", "runtime-failure", "goal"):
        raise ValueError("The handoff demo outcome must be regress, runtime-failure, or goal")
    validate_experiment_limit(max_experiments)
    parent = child_path(workspace, ".runtime", "simulations")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="handoff-", dir=parent))
    print(f"SIMULATION files: {root}")
    (root / "simulation.json").write_text(json.dumps({
        "mode": "simulated", "demo": "research-state handoff", "second_outcome": outcome,
        "description": "Scripted proposals and synthetic results; no model session or ML workload runs.",
        "fixture_repository": "fixture_repo", "copilot_usage": 0, "azure_gpu_hours": 0,
    }, indent=2) + "\n", encoding="utf-8")
    fixture = GitFixture(root)
    baseline_commit = fixture.commit_weight_decay(0.0, "Synthetic baseline for handoff demo")
    rules = EvaluationRules("validation_f1", "maximize", 0.87,
                            (Constraint("latency_ms", "max", 50.0),))
    with Ledger(root, rules, max_experiments=max_experiments) as ledger:
        executor = SimulatedExecutor(root, rules)
        execution = ExecutionController(ledger, executor)
        proposals = HandoffController(ledger, ScriptedProposer)
        write_outputs(ledger.outputs("EXP-000"), rules,
                      JobRequest("EXP-000", None, baseline_commit), score=0.80)
        execution.import_baseline(baseline_commit)
        state = build_research_state(ledger.snapshot())
        handoff = None
        # Two explicitly scripted fixtures, not a general autonomous research loop.
        for index, scenario in enumerate(("improve", outcome), start=1):
            if not state.continuation_allowed:
                break
            parent_record = ledger.best()
            # The second prepared change matches the first scripted proposal.
            commit = fixture.commit_weight_decay(0.01 if index == 1 else 0.02,
                                                 f"Synthetic handoff candidate {index}")
            hypothesis = (handoff.plan.hypothesis if handoff is not None else
                          "Synthetic hypothesis: weight decay may improve validation F1.")
            intervention = (handoff.plan.planned_intervention if handoff is not None else
                            "Set fixture training weight decay to 0.01.")
            candidate = Candidate(parent_record.experiment_id, commit, hypothesis, intervention,
                                  fixture.diff(parent_record.git_commit, commit))
            executor.scenario = scenario
            record = execution.start(candidate)
            for _ in range(3):
                if record.terminal:
                    break
                record = execution.advance(record.experiment_id)
            if not record.terminal:
                raise SliceError("The simulated candidate did not complete as expected")
            state = build_research_state(ledger.snapshot())
            print(f"SIMULATED {record.experiment_id}: {record.decision}; best: {state.best_experiment.experiment_id}")
            if state.continuation_allowed:
                handoff = await proposals.propose_next(fixture.repository)
                with (root / f"handoff-after-{record.experiment_id}.json").open("x", encoding="utf-8") as stream:
                    json.dump(asdict(handoff), stream, indent=2, allow_nan=False)
                    stream.write("\n")
                print(f"Fresh scripted proposal selects parent {handoff.plan.parent_experiment}")
        # This is an inspectable export, never an alternate source of research truth.
        with (root / "research_state.json").open("x", encoding="utf-8") as stream:
            json.dump(asdict(state), stream, indent=2, allow_nan=False)
            stream.write("\n")
        return root, state
