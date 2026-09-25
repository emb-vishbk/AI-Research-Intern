"""Project discovery, Azure choices and durable observation of an existing run."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import threading
import time

from research_intern.domain.experiments import SliceError
from research_intern.execution.discovery import AzureDiscovery, DiscoveryError, selection
from research_intern.execution.outputs import read_json, validate_tree
from research_intern.workspace.discovery import ProjectImporter, metric_values
from research_intern.workspace.paths import child_path
from research_intern.workspace.recovery import atomic_bytes

TERMINAL = {"completed", "failed", "canceled", "cancelled", "notresponding"}


class Onboarding:
    def __init__(self, workspace, mission, connections, *, cloud=None, poll_interval=15):
        self.workspace, self.mission, self.connections = Path(workspace), mission, connections
        self.root = child_path(self.workspace, ".runtime/research-project")
        self.importer = ProjectImporter(self.workspace)
        self.cloud = cloud or AzureDiscovery(lambda: connections.providers.azure_credential)
        self.interval = poll_interval
        self._guard = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    def _read(self):
        path = self.root / "onboarding.json"
        return read_json(path) if path.exists() else {"goal": "", "target": {}, "job_config": "", "existing_run": None}

    def _write(self, state):
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_bytes(self.root / "onboarding.json", json.dumps(state, allow_nan=False, indent=2).encode())

    def snapshot(self):
        with self._guard:
            state = self._read()
            analysis = self.root / "discovery.json"
            state["analysis"] = read_json(analysis) if analysis.exists() else None
            metadata = self.root / "project.json"
            state["import"] = read_json(metadata) if metadata.exists() else None
            state["configured"] = (self.root / "live.json").is_file()
            state["prepared"] = (self.root / "workspace.json").is_file()
            return state

    def scan(self):
        with self.mission.project_operation(), self._guard:
            analysis = self.importer.inspect()
            state = self._read()
            supported = [job for job in analysis["jobs"] if job["supported"]]
            if len(supported) == 1 and not state["job_config"]:
                state["job_config"] = supported[0]["path"]
            state["target"] = {**analysis["azure_hints"], **state["target"]}
            selected = next((job for job in supported if job["path"] == state["job_config"]), {})
            compute = selected.get("compute", "").removeprefix("azureml:")
            if compute and "/" not in compute and compute != "local":
                state["target"].setdefault("compute", compute)
            # Existing operator settings are hints, not a substitute for validation.
            draft = self.workspace / ".runtime/live-settings.draft.json"
            if draft.is_file() and not state["target"].get("workspace_name"):
                for key, value in read_json(draft).get("azure", {}).items():
                    if key in {"subscription_id", "resource_group", "workspace_name", "compute"}:
                        state["target"].setdefault(key, value)
            self._write(state)
        return self.snapshot()

    def upload(self, files, *, archive=False):
        with self.mission.project_operation():
            self.importer.import_files(files, archive=archive)
        return self.scan()

    def save(self, values):
        from research_intern.workspace.project import LoopBudget
        if "budget" in values:
            try:
                LoopBudget(**values["budget"])
            except TypeError as exc:
                raise SliceError("Set the experiment count, GPU hours and AI-credit limits using the three budget fields") from exc
        with self.mission.project_operation(), self._guard:
            if (self.root / "live.json").exists():
                raise SliceError("The active research setup is fixed")
            state = self._read()
            analysis = self.snapshot()["analysis"]
            if not analysis:
                raise SliceError("Inspect the uploaded project first")
            goal = values.get("goal", "").strip()
            if not 1 <= len(goal) <= 4000:
                raise SliceError("Describe your research goal in 1–4,000 characters")
            job_config = values.get("job_config", "")
            if job_config not in {j["path"] for j in analysis["jobs"] if j["supported"]}:
                raise SliceError("Select an Azure command-job YAML whose local code folder is included in the project")
            state.update(goal=goal, job_config=job_config)
            for key in ("metric", "direction", "evaluation_file", "validation_file", "evaluation_command",
                        "evaluation_metrics", "editable", "constraints", "budget", "scoring_python", "job_timeout_seconds",
                        "existing_compatible", "existing_output"):
                if key in values:
                    state[key] = values[key]
            self._write(state)
        return self.snapshot()

    def validate_target(self, target):
        target = selection(target, require_workspace=True)
        if not target.get("compute"):
            raise SliceError("Select or enter the Azure ML compute name")
        with self.mission.project_operation():
            details = self.cloud.validate(target)
            with self._guard:
                if (self.root / "live.json").exists():
                    raise SliceError("The active research workspace and compute are fixed")
                state = self._read()
                if state.get("target") != target:
                    state["existing_run"] = None
                    state["job_search"] = None
                    state["baseline_choice"] = None
                state.update(target=target, target_details=details, target_validated=True)
                self._write(state)
        return self.snapshot()

    def find_jobs(self, *, experiment_name="", job_name=""):
        state = self._read()
        selection(state["target"], require_workspace=True)
        result = self.cloud.jobs(state["target"], experiment_name, job_name)
        with self._guard:
            current = self._read()
            if current["target"] != state["target"]:
                raise SliceError("The selected Azure workspace changed. Search again")
            current["job_search"] = result
            self._write(current)
        return result

    def new_baseline(self):
        with self.mission.project_operation(), self._guard:
            if (self.root / "ledger.sqlite3").exists():
                raise SliceError("The baseline choice is fixed for this research setup")
            state = self._read()
            if state.get("job_search") is None:
                raise SliceError("Search the selected workspace for existing jobs first")
            state.update(existing_run=None, baseline_choice="new", existing_compatible=False)
            self._write(state)
        return self.snapshot()

    def choose_job(self, name):
        selection({"job_name": name})
        with self.mission.project_operation(), self._guard:
            if (self.root / "ledger.sqlite3").exists():
                raise SliceError("The research baseline is already fixed")
            state = self._read()
            if not state.get("target_validated"):
                raise SliceError("Validate the selected workspace and compute first")
            job = self.cloud.job(state["target"], name)
            state.update(baseline_choice="existing", existing_compatible=False)
            state["existing_run"] = {"job": job, "phase": "selected", "message": "Checking the selected Azure job…"}
            self._write(state)
            self.start()
        return self.snapshot()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, name="existing-azure-run", daemon=True)
        self._thread.start()

    def review_baseline(self, compatible, output):
        from research_intern.contracts.models import relative_path
        if output:
            relative_path(output)
        with self.mission.project_operation(), self._guard:
            if (self.root / "ledger.sqlite3").exists():
                raise SliceError("The baseline is already fixed")
            state = self._read()
            state.update(existing_compatible=compatible, existing_output=output)
            self._write(state)
        return self.snapshot()

    def _watch(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                # poll_once persists safe errors for the same selection only.
                pass
            self._stop.wait(self.interval)

    def poll_once(self):
        with self._guard:
            state = self._read()
            previous = state.get("existing_run")
            if not previous or previous.get("phase") == "downloaded":
                return
            target = copy.deepcopy(state["target"])
            name = previous["job"]["name"]
        try:
            job = self.cloud.job(target, name)
            observation = {"job": job, "checked_at": time.time()}
            if job["status"].lower() not in TERMINAL:
                observation.update(phase="waiting", message="Waiting for this Azure job. Copilot is off; no duplicate job will be submitted.")
            else:
                import hashlib
                scope = hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()[:16]
                destination = child_path(self.root, "imported-jobs", scope, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    with tempfile.TemporaryDirectory(dir=destination.parent, prefix="collect-") as temporary:
                        staging = Path(temporary) / "outputs"
                        staging.mkdir()
                        self.cloud.download(target, name, staging)
                        validate_tree(staging)
                        staging.rename(destination)
                metrics, files = [], []
                for path in destination.rglob("*"):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(destination).as_posix()
                    if len(files) < 2000:
                        files.append(relative)
                    if path.suffix == ".json" and any(word in path.name.lower() for word in ("metric", "score", "result")):
                        try:
                            values = metric_values(read_json(path))
                            if values:
                                metrics.append({"path": relative, "values": values})
                        except SliceError:
                            continue
                observation.update(phase="downloaded", message="Job logs and outputs collected. Review its metrics and baseline compatibility before continuing.",
                                   metrics=metrics, files=files, directory=destination.relative_to(self.workspace).as_posix())
        except DiscoveryError as exc:
            observation = {**previous, "phase": "error", "category": exc.category, "message": str(exc)}
        except Exception:
            observation = {**previous, "phase": "error", "message": "Could not collect this run. Check Azure access and available disk space, then retry."}
        with self._guard:
            current = self._read()
            if not self._stop.is_set() and current.get("target") == target and (current.get("existing_run") or {}).get("job", {}).get("name") == name:
                current["existing_run"] = observation
                self._write(current)

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
