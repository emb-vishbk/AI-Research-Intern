"""Local offline run setup and CLI-facing composition; no service authentication."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from research_intern.candidate_demo import prepare_candidate_fixture
from research_intern.contracts.loader import load_contract
from research_intern.controller.candidate import CandidateController, write_evidence
from research_intern.controller.execution import ExecutionController
from research_intern.controller.loop import ResearchLoop, describe_run
from research_intern.copilot.editing import LoopEditor
from research_intern.domain.experiments import JobRequest, SliceError
from research_intern.domain.research import validate_experiment_limit
from research_intern.execution.simulated import SimulatedExecutor, write_outputs
from research_intern.ledger.preparations import LOOP_SCENARIOS, PreparationJournal
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.git import GitWorkspace
from research_intern.workspace.paths import child_path


def run_directory(workspace: Path, directory: Path) -> Path:
    runtime = child_path(workspace, ".runtime")
    try:
        return child_path(runtime, directory.absolute().relative_to(runtime).as_posix())
    except (ValueError, SliceError) as exc:
        raise SliceError("Select an existing offline run inside this workspace's .runtime/") from exc


def initialize_run(workspace: Path, *, max_experiments: int = 4, max_attempts: int | None = None,
                   scenario: str = "mixed") -> Path:
    validate_experiment_limit(max_experiments)
    max_attempts = max_experiments * 2 if max_attempts is None else max_attempts
    validate_experiment_limit(max_attempts)
    if scenario not in LOOP_SCENARIOS:
        raise ValueError("Unknown offline loop scenario")
    parent = child_path(workspace, ".runtime", "simulations")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="loop-", dir=parent)).resolve()
    write_evidence(root / "simulation.json", {"mode": "simulated", "demo": "bounded offline research loop",
                                            "copilot_usage": 0, "azure_gpu_hours": 0,
                                            "description": "Mock editing and synthetic scores; no researcher code is executed."})
    repository, baseline = prepare_candidate_fixture(root, max_experiments=max_experiments)
    git = GitWorkspace(root, repository)
    git.verify_clean(baseline)
    contract = load_contract(repository)
    with Ledger(root, contract.rules, contract=contract, repository=repository) as ledger:
        PreparationJournal(ledger).configure(max_attempts=max_attempts, scenario=scenario)
        execution = ExecutionController(ledger, SimulatedExecutor(root, contract.rules, paths=contract.outputs))
        write_outputs(ledger.outputs("EXP-000"), contract.rules, JobRequest("EXP-000", None, baseline),
                      score=0.80, paths=contract.outputs)
        execution.import_baseline(baseline)
    return root


def compose_loop(ledger: Ledger, *, emit=lambda message: None) -> ResearchLoop:
    policy = PreparationJournal(ledger).policy()
    if policy is None or ledger.contract is None or ledger.repository is None:
        raise SliceError("The selected run needs a persisted loop policy, contract, and repository")
    outcomes = {"goal": ("goal",), "runtime-failure": ("runtime-failure",)}.get(
        policy["scenario"], ("improve", "regress", "runtime-failure", "goal"))
    git = GitWorkspace(ledger.root, ledger.repository)
    candidates = CandidateController(ledger, git, lambda: LoopEditor(policy["scenario"]))
    execution = ExecutionController(ledger, SimulatedExecutor(ledger.root, ledger.rules,
                                                              paths=ledger.output_paths, scenarios=outcomes))
    return ResearchLoop(candidates, execution, emit=emit)


async def resume_run(workspace: Path, directory: Path, *, steps: int | None = None, emit=print) -> dict:
    root = run_directory(workspace, directory)
    with Ledger.reopen(root) as ledger:
        return await compose_loop(ledger, emit=emit).run(steps=steps)


def inspect_run(workspace: Path, directory: Path, *, stop: bool = False) -> dict:
    with Ledger.reopen(run_directory(workspace, directory)) as ledger:
        describe_run(ledger)  # Reject legacy demos before writing a stop flag.
        if stop:
            ledger.request_stop()
        return describe_run(ledger)


def print_status(status: dict, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(status, indent=2, allow_nan=False))
        return
    state, controller, attempts = status["research_state"], status["controller"], status["preparation_budget"]
    print(f"SIMULATED run: {status['run_directory']}")
    print(f"Last controller state: {controller['state']}" + (f" — {controller['message']}" if controller["message"] else ""))
    print(f"Experiment slots: {state['budget']['allocated_experiments']}/{state['budget']['max_experiments']}; "
          f"preparations: {attempts['used']}/{attempts['limit']}")
    best, latest = state["best_experiment"], state["last_experiment"]
    if best:
        print(f"Best: {best['experiment_id']} ({best['score']}); latest: {latest['experiment_id']} ({latest['decision'] or latest['state']})")
    if state["blocking_reasons"]:
        print("New proposals blocked: " + ", ".join(state["blocking_reasons"]))
    print("Synthetic evidence only. Copilot usage: 0; Azure GPU-hours: 0.")
