"""Generate the internal contract and execution settings from reviewed UI choices."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
import tempfile
import shutil

import yaml

from research_intern.contracts.loader import load_contract, read_yaml
from research_intern.contracts.models import CONTRACT_PATH, ExperimentContract, relative_path
from research_intern.domain.experiments import SliceError
from research_intern.evaluation.trusted import digest
from research_intern.evaluation.runtime import prepare_runtime
from research_intern.execution.native import validate_native
from research_intern.live import validate_settings
from research_intern.workspace.importing import (contract_digest, prepared_workspace, prepare_source,
                                                publish_preparation, require_draft, resume_preparation)
from research_intern.workspace.git import snapshot_tree
from research_intern.workspace.lock import RunLock
from research_intern.workspace.paths import child_path
from research_intern.workspace.project import LoopBudget
from research_intern.workspace.recovery import atomic_bytes
from research_intern.workspace.autoscoring import discover, source_file


def generated_settings(onboarding, state, contract, files, repository=None):
    connections = onboarding.connections.snapshot()
    if connections["azure"]["status"] != "connected" or connections["copilot"]["status"] != "connected":
        raise SliceError("Connect Azure and GitHub Copilot before preparing research")
    agent = connections["copilot"]
    if not agent.get("selected_model"):
        raise SliceError("Choose an available Copilot coding model in the account section")
    prior, _, _ = onboarding.connections.providers.settings()
    runtime = agent.get("runtime_path") or (prior or {}).get("copilot", {}).get("runtime_path")
    if not runtime:
        raise SliceError("Reconnect Copilot to finish preparing its runtime")
    if not contract.execution.native:
        if not prior:
            raise SliceError("This previously prepared project is missing its stored Azure input bindings. Its original workload YAML and data references are needed to configure execution")
        result = dict(prior)
        result["azure"] = {**result["azure"], **state["target"]}
        result["copilot"] = {**result["copilot"], "runtime_path": runtime, "model": agent["selected_model"]}
        return validate_settings(result, onboarding.workspace)
    budget = contract.budget
    turns = max(1, min(1000, budget.max_experiments * 2))
    gpu = state["target_details"].get("gpu_count")
    if type(gpu) is not int or gpu < 1:
        raise SliceError("Select a provisioned GPU compute cluster and validate its GPU count")
    timeout = state.get("job_timeout_seconds", 3600)
    if timeout * gpu > budget.max_gpu_hours * 3600:
        raise SliceError("The per-job timeout reserves more GPU time than your total budget. Reduce it or increase the GPU budget")
    protected = [name for name in files if not contract.allows(name)]
    repository = repository or onboarding.root / "repository"
    readable = []
    for name in files:
        path = repository / name
        if name == contract.evaluation.validation_split or path.stat().st_size > 128 * 1024:
            continue
        try:
            path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
        readable.append(name)
    editable = set(contract.scope.editable)
    if not editable.issubset(readable) or len(editable) > 500:
        raise SliceError("Choose at most 500 editable UTF-8 files, each up to 128 KiB")
    readable = sorted(editable) + [p for p in readable if p not in editable][:500 - len(editable)]
    result = {
        "azure": {**state["target"], "environment": "", "image": "", "dataset": "", "weights": "",
                  "inputs": {}, "native_job": True, "timeout_seconds": timeout, "gpu_count": gpu},
        "copilot": {"runtime_path": runtime, "model": agent["selected_model"], "readable_paths": readable,
                    "max_turns": turns, "timeout_seconds": 600},
        "scoring": {"python": state.get("prepared_scoring_python") or state.get("scoring_python") or sys.executable, "files": protected, "timeout_seconds": 3600},
    }
    return validate_settings(result, onboarding.workspace)


def build_contract(repository, assets, metadata, state):
    """Only operates on a temporary source copy. Never executes researcher code."""
    validate_native(repository, state["job_config"])
    budget = LoopBudget(**state.get("budget", {}))
    if not budget.max_experiments or not budget.max_gpu_hours or not budget.max_ai_credits:
        raise SliceError("Set positive experiment, GPU-hour and AI-credit limits")
    plan = discover(repository, assets, state, metadata)
    if plan["status"] != "ready":
        raise SliceError(plan["message"])
    state.update(evaluation_file=plan["evaluator"], validation_file=plan["reference"], evaluation_setup=plan)
    # Import helpers and references may have been retained as assets (e.g. a
    # project's src/data package). Promote only this statically discovered closure.
    for name in plan["protected"]:
        target = child_path(repository, name)
        original = source_file(repository, assets, name)
        if not target.is_file() and original.is_file():
            if original.stat().st_size > 16 * 1024**2:
                raise SliceError(f"Evaluator reference exceeds the source bundle limit: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
    for field, label in (("evaluation_file", "evaluation script"), ("validation_file", "validation split or reference file")):
        if not state.get(field):
            raise SliceError(f"Select the {label} from your project")
        name = relative_path(state[field])
        target = child_path(repository, name)
        if not target.is_file():
            original = child_path(assets, name)
            if not original.is_file() or original.stat().st_size > 16 * 1024**2:
                raise SliceError(f"Select a {label} up to 16 MiB that is included in the project")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
    reference, evaluator = state["validation_file"], state["evaluation_file"]
    editable = sorted(set(relative_path(p) for p in state.get("editable", [])))
    if not editable or any(not child_path(repository, p).is_file() for p in editable):
        raise SliceError("Select at least one existing training or configuration file the Intern may change")
    if evaluator in editable or reference in editable or state["job_config"] in editable:
        raise SliceError("The evaluator, validation reference and Azure job definition must remain fixed")
    editable = [name for name in editable if name not in plan["protected"]]
    if not editable:
        raise SliceError("The selected editable files all belong to fixed evaluation. Select the training code or training configuration to improve.")
    internal = repository / ".research_intern"
    internal.mkdir(exist_ok=True)
    command = plan["command"]
    if command:
        arguments = shlex.split(command)
        if not arguments or not any(evaluator == arg for arg in arguments):
            raise SliceError("The evaluation command must name the selected evaluation script using its project-relative path")
        relative_path(state.get("evaluation_metrics", "metrics.json"))
        spec = plan["spec"]
        (internal / "scoring.json").write_text(json.dumps(spec), encoding="utf-8")
        shutil.copyfile(Path(__file__).with_name("score_runner.py"), internal / "score_runner.py")
        entry = ".research_intern/score_runner.py"
    else:
        entry = evaluator
    shutil.copyfile(Path(__file__).with_name("job_runner.py"), internal / "job_runner.py")
    job_path = repository / state["job_config"]
    job = read_yaml(job_path)
    local_inputs = []
    for binding in job.get("inputs", {}).values():
        if isinstance(binding, dict) and isinstance(binding.get("path"), str):
            resolved = (job_path.parent / binding["path"]).resolve()
            if resolved.is_relative_to(repository.resolve()):
                local_inputs.append(resolved.relative_to(repository.resolve()).as_posix())
    historical = {"outputs", "results", "runs", "logs", "artifacts"}
    manifest = {name: value for name, value in metadata.get("assets", {}).items()
                if not child_path(repository, name).exists()
                and (not any(p.casefold() in historical for p in Path(name).parts[:-1])
                     or any(name == p or name.startswith(p.rstrip("/") + "/") for p in local_inputs))}
    (internal / "assets.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    # Git is app-owned. Preserve original ignore rules as evidence, then allow
    # only the files already admitted by the importer into the source snapshot.
    ignore_archive = internal / "original-ignore-rules.json"
    ignores = json.loads(ignore_archive.read_text(encoding="utf-8")) if ignore_archive.exists() else {}
    filtered_notice = "# Source was filtered by Research Intern during import.\n"
    for path in repository.rglob(".gitignore"):
        original = path.read_text(encoding="utf-8")
        if original != filtered_notice:
            ignores[path.relative_to(repository).as_posix()] = original
        path.write_text(filtered_notice, encoding="utf-8")
    if ignores:
        ignore_archive.write_text(json.dumps(ignores), encoding="utf-8")
    files = sorted(p.relative_to(repository).as_posix() for p in repository.rglob("*") if p.is_file())
    protected = [p for p in files if p not in editable and not p.startswith(".research_intern/")]
    value = {"version": "1.0", "goal": state["goal"],
             "objective": {"metric": state.get("metric", ""), "direction": state.get("direction", "maximize")},
             "constraints": state.get("constraints", {}), "budget": state["budget"],
             "execution": {"backend": "azure_ml", "job_config": state["job_config"], "native": True},
             "outputs": {"root": ".ri_outputs/"}, "scope": {"editable": editable, "protected": protected},
             "evaluation": {"metric_definition": state.get("metric", "") + ": computed by the reviewed evaluator",
                            "procedure": command or "Independent evaluator using --outputs, --request and --result",
                            "dataset_version": "SHA-256:" + digest(repository / reference),
                            "evaluator": entry, "evaluator_sha256": digest(repository / entry),
                            "validation_split": reference, "validation_split_sha256": digest(repository / reference)}}
    contract = ExperimentContract.from_dict(value)
    (repository / CONTRACT_PATH).write_text(yaml.safe_dump(contract.to_dict(), sort_keys=False), encoding="utf-8")
    load_contract(repository)
    return contract


def prepare_discovered(onboarding, *, authorized=False):
    if authorized is not True:
        raise SliceError("Review and approve the displayed research setup first")
    with onboarding.mission.project_operation(), onboarding._guard:
        state = onboarding.snapshot()
        if not state.get("analysis") or not state.get("goal"):
            raise SliceError("Inspect the project and save your goal and file selections first")
        if not state.get("target_validated"):
            raise SliceError("Validate the selected Azure workspace and compute first")
        if state.get("baseline_choice") not in {"new", "existing"}:
            raise SliceError("Find existing Azure jobs, then choose a run or explicitly choose a new baseline")
        if state.get("baseline_choice") == "existing" and not state.get("existing_compatible"):
            raise SliceError("Confirm that the selected run used this source and the same evaluation data before reusing its results")
        selected = state.get("existing_run") or {}
        if selected.get("phase") == "downloaded" and selected.get("job", {}).get("status", "").lower() != "completed":
            raise SliceError("The selected run did not complete successfully. Review its logs, then choose a completed run or a new baseline")
        projects = onboarding.mission.projects
        root, repository = onboarding.root, onboarding.root / "repository"
        if (root / "live.json").is_file():
            return onboarding.snapshot()
        # Source preparation predates the final UI choices in older workspaces.
        # Before live configuration, rebuild from the human-reviewed fields and
        # retain the entire prior preparation. Active runs remain immutable.
        with RunLock(root):
            require_draft(root)
            resume_preparation(root)
            prepared = prepared_workspace(root, repository)
            original = snapshot_tree(repository, omit_git=True)
            with tempfile.TemporaryDirectory(dir=root, prefix="setup-") as temporary:
                staged = Path(temporary) / "repository"
                shutil.copytree(repository, staged, ignore=shutil.ignore_patterns(".git"))
                contract = build_contract(staged, root / "assets", state["import"] or {}, state)
                if state.get("baseline_choice") == "existing" and selected.get("phase") == "downloaded":
                    from research_intern.workspace.score_runner import artifact_file
                    outputs = child_path(onboarding.workspace, selected["directory"])
                    if not outputs.is_relative_to(root / "imported-jobs"):
                        raise SliceError("The selected job's outputs must be inside this project's download directory")
                    if hasattr(onboarding.cloud, "ensure_outputs"):
                        onboarding.cloud.ensure_outputs(state["target"], selected["job"]["name"], outputs)
                    selected_output = child_path(outputs, state["existing_output"]) if state.get("existing_output") else outputs
                    for candidates in state["evaluation_setup"]["spec"]["artifacts"].values():
                        try:
                            artifact_file(selected_output, candidates)
                        except RuntimeError as exc:
                            raise SliceError(str(exc) + " Choose a run with saved evaluation artifacts, or choose a new baseline.") from exc
                files = sorted(p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file())
                settings = generated_settings(onboarding, state, contract, files, staged)
                # Fail before publishing if the chosen scorer exceeds its bundle limit.
                scorer_files = [staged / p for p in settings["scoring"]["files"]]
                if any(p.stat().st_size > 16 * 1024**2 for p in scorer_files) or sum(p.stat().st_size for p in scorer_files) > 64 * 1024**2:
                    raise SliceError("The fixed evaluator source and reference must fit a 64 MiB bundle (16 MiB per file)")
                from research_intern.validation.preflight import validate_files
                validate_files(staged, tuple(files))
                # Only explicit preparation installs dependencies, never upload or
                # inspection. Fail before publishing if the scoring runtime fails.
                settings["scoring"]["python"] = prepare_runtime(root, state["evaluation_setup"]["runtime"], state.get("scoring_python", ""))
                if prepared is not None:
                    if snapshot_tree(staged, omit_git=True) != original:
                        prepare_source(Path(temporary), staged, contract_digest(staged))
                        publish_preparation(root, Path(temporary), prepared)
                else:
                    if snapshot_tree(repository, omit_git=True) != original:
                        raise SliceError("Project source changed during preparation. Review the changes and retry")
                    for name in files:
                        target = child_path(repository, name)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        atomic_bytes(target, (staged / name).read_bytes())
                    prepare_source(root, repository, contract_digest(repository))
        project = projects.inspect()
        projects.save_budget(LoopBudget(**state["budget"]), project["contract_sha256"])
        projects.confirm_evaluation(project["contract_sha256"], project["source_commit"], project["evaluation_fingerprint"])
        onboarding.mission.live.configure(settings, authorized=True)
        onboarding.connections.providers.bind(settings)
        current = onboarding._read()
        current.update(evaluation_file=state["evaluation_file"], validation_file=state["validation_file"],
                       evaluation_setup=state["evaluation_setup"], editable=list(contract.scope.editable))
        current["setup_complete"] = True
        onboarding._write(current)
    return onboarding.snapshot()
