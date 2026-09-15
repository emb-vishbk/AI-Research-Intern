"""Offline proof of contract enforcement, committed candidates, and parent restoration."""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from research_intern.contracts.loader import load_contract
from research_intern.controller.candidate import CandidateController, write_evidence
from research_intern.controller.execution import ExecutionController
from research_intern.copilot.editing import ScriptedEditor
from research_intern.domain.experiments import JobRequest, SliceError
from research_intern.domain.research import ResearchState, validate_experiment_limit
from research_intern.execution.simulated import SimulatedExecutor, write_outputs
from research_intern.ledger.research_state import build_research_state
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.fixture import GitFixture
from research_intern.workspace.git import GitWorkspace, PermissionViolation
from research_intern.workspace.paths import child_path


def prepare_candidate_fixture(root: Path, *, max_experiments: int = 3) -> tuple[Path, str]:
    """Provision one prepared experiment. This is setup, never an experiment ID."""
    validate_experiment_limit(max_experiments)
    fixture = GitFixture(root)
    repository = fixture.repository
    (repository / ".research_intern").mkdir()
    (repository / "data" / "test").mkdir(parents=True)
    (repository / "configs").mkdir()
    (repository / "labeller.py").write_text("WEIGHT_DECAY = 0.0\n", encoding="utf-8")
    (repository / "evaluate.py").write_text("# Protected evaluation fixture, not a real evaluator.\n", encoding="utf-8")
    (repository / "data" / "test" / "annotations.json").write_text("{}\n", encoding="utf-8")
    (repository / "configs" / "training.json").write_text('{"batch_size": 4}\n', encoding="utf-8")
    (repository / "azure_job.yaml").write_text("type: command\ncommand: python labeller.py\n", encoding="utf-8")
    (repository / ".gitignore").write_text("*.cache\n", encoding="utf-8")
    (repository / ".research_intern" / "contract.yaml").write_text(f'''version: "1.0"
objective:
  metric: validation_f1
  direction: maximize
  target: 0.87
execution:
  backend: azure_ml
  job_config: azure_job.yaml
outputs:
  root: experiment_outputs/
scope:
  editable: [labeller.py, configs/]
  protected: [evaluate.py, data/test/]
constraints:
  latency_ms:
    max: 50
budget:
  max_experiments: {max_experiments}
  max_gpu_hours: 1
''', encoding="utf-8")
    fixture._git("add", "--all")
    fixture._git("commit", "-m", "Prepared synthetic baseline for controlled candidate demo")
    return repository, fixture._git("rev-parse", "HEAD")


async def run_candidate_demo(workspace: Path, *, scenario: str = "regress") -> tuple[Path, ResearchState]:
    if scenario not in ("allowed", "protected", "regress"):
        raise ValueError("Unknown candidate demo scenario")
    simulations = child_path(workspace, ".runtime", "simulations")
    simulations.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="candidate-", dir=simulations)).resolve()
    print(f"SIMULATION files: {root}")
    write_evidence(root / "simulation.json", {"mode": "simulated", "demo": "controlled candidate",
                                            "scenario": scenario, "copilot_usage": 0, "azure_gpu_hours": 0,
                                            "description": "Real local file edits and Git commits; synthetic proposals and scores."})
    repository, baseline = prepare_candidate_fixture(root)
    git = GitWorkspace(root, repository)
    git.verify_clean(baseline)
    contract = load_contract(repository)
    with Ledger(root, contract.rules, contract=contract, repository=repository) as ledger:
        executor = SimulatedExecutor(root, contract.rules, paths=contract.outputs)
        execution = ExecutionController(ledger, executor)
        write_outputs(ledger.outputs("EXP-000"), contract.rules, JobRequest("EXP-000", None, baseline),
                      score=0.80, paths=contract.outputs)
        execution.import_baseline(baseline)
        scenarios = ("improve", "regress") if scenario == "regress" else ("improve",)
        # A fixed acceptance demonstration, not an autonomous search loop.
        for index, outcome in enumerate(scenarios, start=1):
            proposer = lambda: ScriptedEditor(0.01 * index, path="evaluate.py" if scenario == "protected" else "labeller.py")
            controller = CandidateController(ledger, git, proposer)
            try:
                record = await controller.prepare_candidate()
            except PermissionViolation as exc:
                if scenario != "protected":
                    raise
                print(f"BLOCKED before execution: {exc}")
                break
            if scenario == "protected":
                raise SliceError("The protected edit unexpectedly passed validation")
            executor.scenario = outcome
            record = execution.resume_submission(record.experiment_id)
            for _ in range(3):
                if record.terminal:
                    break
                record = execution.advance(record.experiment_id)
            if not record.terminal:
                raise SliceError("The simulated candidate did not finish")
            print(f"SIMULATED {record.experiment_id}: {record.decision}; commit: {record.git_commit}")
        state = build_research_state(ledger.snapshot())
        if scenario == "regress":
            parent = git.prepare_parent(state.best_experiment.git_commit,
                                        known_commits={r.git_commit for r in ledger.history()})
            write_evidence(root / "restored_parent.json", {"selected_parent": state.best_experiment.experiment_id,
                                                         "git_commit": parent.commit})
            print(f"Workspace restored to best {state.best_experiment.experiment_id}; latest: {state.last_experiment.experiment_id}")
        (root / "research_state.json").write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")
        return root, state
