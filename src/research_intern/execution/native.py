"""Run an ordinary Azure command-job YAML with app-owned provenance wrapping."""
from pathlib import Path
import json
import io
import re
import shlex
import shutil
import yaml

from research_intern.execution.adapter import ExecutionError
from research_intern.contracts.loader import read_yaml
from research_intern.evaluation.trusted import digest
from research_intern.workspace.paths import child_path


def validate_native(repository, path):
    file = child_path(repository, path)
    job = read_yaml(file)
    if not isinstance(job, dict) or job.get("type", "command") != "command" or not isinstance(job.get("command"), str):
        raise ExecutionError("Select a command-job YAML; pipelines and sweeps need a command job first")
    if any(not isinstance(job.get(key, {}), dict) for key in ("resources", "inputs", "outputs")):
        raise ExecutionError("The job's resources, inputs and outputs must be YAML mappings")
    if job.get("distribution") or job.get("resources", {}).get("instance_count", 1) != 1:
        raise ExecutionError("Select a single-node command job so its compute usage can be bounded")
    code = job.get("code", ".")
    root = (file.parent / code).resolve() if isinstance(code, str) else None
    if root is None or not root.is_relative_to(repository.resolve()) or not root.is_dir():
        raise ExecutionError("The YAML's local code folder must be included in the uploaded project")
    environment = job.get("environment")
    if not isinstance(environment, (str, dict)) or not environment:
        raise ExecutionError("The selected YAML needs an Azure ML environment")
    if isinstance(environment, str) and ("@latest" in environment or "/labels/" in environment):
        raise ExecutionError("Use a specific environment version in your YAML so experiments share the same runtime")
    if isinstance(environment, dict):
        local_paths = [environment.get("conda_file"), (environment.get("build") or {}).get("path")]
        for value in local_paths:
            if isinstance(value, str) and not (file.parent / value).resolve().is_relative_to(repository.resolve()):
                raise ExecutionError("Environment build and conda files must be included in the project")
    for name, binding in job.get("inputs", {}).items():
        if name.startswith("ri_"):
            raise ExecutionError("Input names starting with ri_ are reserved for experiment tracking")
        if not isinstance(binding, dict) or "path" not in binding:
            continue
        value = str(binding["path"])
        if value.startswith(("azureml:", "https://", "http://", "wasbs://", "abfss://")):
            if "@latest" in value or "/labels/" in value or "?" in value:
                raise ExecutionError("Use versioned Azure inputs without embedded access tokens")
            continue
        resolved = (file.parent / value).resolve()
        if not resolved.is_relative_to(repository.resolve()):
            raise ExecutionError(f"Input {name} points outside the project. Include it or use an Azure data asset")
    for name, value in job.get("outputs", {}).items():
        if name == "ri_results" or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            raise ExecutionError("Output names must be simple identifiers; ri_results is reserved")
        value = value or {}
        if not isinstance(value, dict) or value.get("type", "uri_folder") != "uri_folder":
            raise ExecutionError("Use uri_folder job outputs to collect checkpoints, predictions and logs")
        if value.get("path"):
            raise ExecutionError("Remove fixed output paths from the job YAML so candidates cannot overwrite earlier results")
    return {"path": path, "code_root": root.relative_to(repository.resolve()).as_posix()}


def stage_assets(run_root, source):
    manifest = json.loads((source / ".research_intern/assets.json").read_text())
    for name, expected in manifest.items():
        original, target = child_path(run_root / "assets", name), child_path(source, name)
        if target.exists() or not original.is_file() or original.stat().st_nlink != 1:
            raise ExecutionError(f"Retained input is missing or conflicts with source: {name}")
        if original.stat().st_size != expected["bytes"] or digest(original) != expected["sha256"]:
            raise ExecutionError(f"Retained input changed: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        if digest(target) != expected["sha256"]:
            raise ExecutionError(f"Retained input changed during upload preparation: {name}")


def submit_native(client, name, code, payload, tags):
    from azure.ai.ml import load_job
    source = Path(code) / "source"
    spec = payload["native_job"]
    definition = read_yaml(source / spec["path"])
    request = payload["request"]
    original_command = definition["command"]
    if any(key.startswith("ri_") for key in (definition.get("inputs") or {})) or "ri_results" in (definition.get("outputs") or {}):
        raise ExecutionError("The ri_ input prefix and ri_results output are reserved for generated job tracking")
    arguments = ["python", ".research_intern/job_runner.py", "--command", original_command,
                 "--cwd", spec["code_root"], "--output", "${{outputs.ri_results}}",
                 "--experiment-id", request["experiment_id"], "--parent", request["parent_experiment"] or "none",
                 "--commit", request["git_commit"], "--fingerprint", payload["evaluation_fingerprint"]]
    for output in definition.get("outputs") or {}:
        arguments.extend(["--original-output", output, "${{outputs." + output + "}}"])
    definition.update(command="cd source && " + " ".join(shlex.quote(str(item)) for item in arguments),
                      code=str(code), name=name, compute=payload["settings"]["compute"],
                      display_name=request["experiment_id"] + " · " + (definition.get("display_name") or definition.get("experiment_name") or "Research Intern"),
                      limits={"timeout": payload["settings"]["timeout_seconds"]},
                      tags={**(definition.get("tags") or {}), **tags},
                      outputs={**(definition.get("outputs") or {}), "ri_results": {"type": "uri_folder", "mode": "rw_mount"}})
    # Declare new ports before deserialization: SDK Command.outputs only accepts
    # names that were declared when its component was constructed.
    job = load_job(io.StringIO(yaml.safe_dump(definition)), relative_origin=str((source / spec["path"]).parent))
    return client.jobs.create_or_update(job)
