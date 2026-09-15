"""One synthetic baseline/candidate demo, with real local fixture Git commits."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from research_intern.controller.execution import ExecutionController
from research_intern.domain.experiments import (
    Candidate, Constraint, EvaluationRules, ExperimentRecord, JobRequest, SliceError,
)
from research_intern.execution.simulated import SimulatedExecutor, write_outputs
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.paths import child_path
from research_intern.workspace.fixture import prepare_git_fixture


def run_demo(workspace: Path, scenario: str = "improve") -> tuple[Path, ExperimentRecord, str]:
    parent = child_path(workspace, ".runtime", "simulations")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="execution-", dir=parent))
    print(f"SIMULATION files: {root}")
    (root / "simulation.json").write_text(json.dumps({
        "mode": "simulated", "scenario": scenario,
        "description": "Synthetic results; no ML code, Copilot session, or Azure job is run.",
        "fixture_repository": "fixture_repo",
        "copilot_usage": 0, "azure_gpu_hours": 0,
    }, indent=2) + "\n", encoding="utf-8")
    baseline_commit, candidate_commit, diff = prepare_git_fixture(root)
    rules = EvaluationRules("validation_f1", "maximize", 0.87,
                            (Constraint("latency_ms", "max", 50.0),))
    with Ledger(root, rules, max_experiments=1) as ledger:
        executor = SimulatedExecutor(root, rules, scenario)
        controller = ExecutionController(ledger, executor)
        write_outputs(ledger.outputs("EXP-000"), rules, JobRequest("EXP-000", None, baseline_commit),
                      score=0.80)
        controller.import_baseline(baseline_commit)
        candidate = Candidate("EXP-000", candidate_commit,
                              "Synthetic hypothesis: weight decay may improve validation F1.",
                              "Change the fixture's weight decay from 0.0 to 0.01.", diff)
        record = controller.start(candidate)
        # Only poll this one simulated candidate. This is not the autonomous loop.
        for _ in range(3):
            if record.terminal:
                break
            record = controller.advance(record.experiment_id)
        if not record.terminal:
            raise SliceError("The simulation did not finish within its expected polling steps")
        return root, record, ledger.best().experiment_id
