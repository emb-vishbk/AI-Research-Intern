"""Reuse downloaded baseline artifacts without submitting or billing a new job.

Historical source association is user-attested and recorded as such. The score
still comes from the same frozen evaluator used for subsequent candidates.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile

from research_intern.domain.experiments import SliceError
from research_intern.evaluation.trusted import inventory
from research_intern.execution.outputs import read_json, validate_tree
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes


def freeze_existing(root, workspace, config):
    path = root / "onboarding.json"
    if not path.exists():
        return None
    state = read_json(path)
    if state.get("baseline_choice") != "existing":
        return None
    run = state.get("existing_run") or {}
    if run.get("phase") != "downloaded":
        raise SliceError("Wait for the selected Azure job and its outputs to finish downloading. No duplicate will be submitted")
    if run.get("job", {}).get("status", "").lower() != "completed":
        raise SliceError("The selected run failed or was cancelled. Inspect its logs, then choose a completed run or a new baseline before preparation")
    if not state.get("existing_compatible"):
        raise SliceError("Confirm the historical run's source and validation-data association before reusing its results")
    azure = config["settings"]["azure"]
    if any(azure.get(k) != v for k, v in state["target"].items()):
        raise SliceError("The selected run belongs to a different Azure target")
    download = child_path(workspace, run["directory"])
    if not download.is_relative_to(root / "imported-jobs"):
        raise SliceError("The selected run's artifacts must be inside its owned download directory")
    if state.get("existing_output"):
        output = child_path(download, state["existing_output"])
    else:
        named = download / "named-outputs"
        folders = list(named.iterdir()) if named.is_dir() else []
        if len(folders) == 1 and folders[0].is_dir():
            output = folders[0]
        elif folders:
            raise SliceError("Choose the results folder within this run's download; more than one named output was found")
        else:
            output = download
    validate_tree(output)
    artifacts = inventory(output)
    if not artifacts:
        raise SliceError("The selected run contains no artifacts to evaluate")
    receipt = {"job": run["job"], "target": state["target"], "artifacts": artifacts,
               "directory": output.relative_to(root).as_posix(), "source_association": "user_attested",
               "associated_source_commit": config["source_commit"],
               "evaluation_fingerprint": config["evaluation_fingerprint"]}
    saved = root / "imported-baseline.json"
    if saved.exists() and read_json(saved) != receipt:
        raise SliceError("Previously selected baseline evidence changed")
    atomic_bytes(saved, json.dumps(receipt, indent=2).encode())
    return receipt


class ImportedBaselineExecutor:
    backend = "azure_ml"

    def __init__(self, root, delegate):
        self.root, self.delegate = root, delegate
        self.receipt = read_json(root / "imported-baseline.json")
        self.job_id = self.receipt["job"]["name"]

    def _check(self, request):
        if request.git_commit != self.receipt["associated_source_commit"] or request.parent_experiment is not None:
            raise SliceError("Historical baseline association differs from the approved source")
        source = child_path(self.root, self.receipt["directory"])
        if inventory(source) != self.receipt["artifacts"]:
            raise SliceError("Downloaded baseline artifacts changed")

    def submit_job(self, request):
        if request.experiment_id != "EXP-000":
            return self.delegate.submit_job(request)
        self._check(request)
        return self.job_id  # Existing job; no submission and no GPU reservation.

    def find_job(self, request):
        if request.experiment_id != "EXP-000":
            return self.delegate.find_job(request)
        self._check(request)
        return self.job_id

    def get_status(self, job_id):
        return "completed" if job_id == self.job_id else self.delegate.get_status(job_id)

    def cancel_job(self, job_id):
        if job_id != self.job_id:
            self.delegate.cancel_job(job_id)

    def download_outputs(self, job_id, destination):
        if job_id != self.job_id:
            return self.delegate.download_outputs(job_id, destination)
        source = child_path(self.root, self.receipt["directory"])
        if inventory(source) != self.receipt["artifacts"]:
            raise SliceError("Downloaded baseline artifacts changed")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix="adopt-") as temporary:
            stage = Path(temporary) / "outputs"
            shutil.copytree(source, stage)
            original = stage / "run.json"
            if original.exists():
                original.unlink()  # Original remains intact in the hashed download.
            original.write_text(json.dumps({"experiment_id": "EXP-000", "parent_experiment": None,
                "source_commit": self.receipt["associated_source_commit"],
                "source_association": "user_attested", "original_job_id": self.job_id,
                "evaluation_fingerprint": self.receipt["evaluation_fingerprint"], "status": "completed"}))
            stage.rename(destination)
