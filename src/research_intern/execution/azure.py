"""Opt-in Azure command-job boundary with exact local source and durable intent.

The SDK is imported only by AzureMLGateway. Offline tests supply a fake gateway.
LiveProject composes this adapter into the same serial controller used offline.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from research_intern.contracts.loader import load_contract, read_yaml
from research_intern.domain.experiments import JobRequest
from research_intern.execution.adapter import ExecutionError, JobStatus
from research_intern.execution.outputs import validate_tree
from research_intern.ledger.services import ServiceJournal
from research_intern.workspace.git import GitWorkspace, snapshot_tree
from research_intern.workspace.lock import RunLock
from research_intern.workspace.paths import child_path


@dataclass(frozen=True)
class AzureSettings:
    subscription_id: str
    resource_group: str
    workspace_name: str
    compute: str
    environment: str
    image: str
    dataset: str
    weights: str
    timeout_seconds: int
    gpu_count: int
    inputs: dict | None = None
    native_job: bool = False

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", self.subscription_id):
            raise ExecutionError("Supply the approved Azure subscription ID")
        for name in ("resource_group", "workspace_name", "compute"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", getattr(self, name)):
                raise ExecutionError("Azure workspace and compute names must be explicit")
        if type(self.native_job) is not bool:
            raise ExecutionError("native_job must be a boolean")
        for name in (() if self.native_job else (("environment", "dataset", "weights") if self.inputs is None else ("environment",))):
            if not re.fullmatch(r"azureml:[A-Za-z0-9][A-Za-z0-9_.-]*:[0-9]+", getattr(self, name)):
                raise ExecutionError("Use workspace assets with explicit numeric versions, not URLs or latest")
        if not self.native_job and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[0-9a-f]{64}", self.image):
            raise ExecutionError("Pin the container image by SHA-256 digest")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 86400:
            raise ExecutionError("Declare a job timeout between 1 and 86400 seconds")
        if type(self.gpu_count) is not int or not 1 <= self.gpu_count <= 8:
            raise ExecutionError("Declare and verify the compute's GPU count")
        if self.inputs is not None:
            if not isinstance(self.inputs, dict) or any(not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", k) for k in self.inputs):
                raise ExecutionError("Declare named input bindings")
            if set(self.inputs) & {"experiment_id", "parent_experiment", "source_commit"}:
                raise ExecutionError("Experiment identity is controller-owned")
            for value in self.inputs.values():
                if isinstance(value, dict):
                    if set(value) == {"baseline", "candidate"}:
                        from research_intern.contracts.models import relative_path
                        for path in value.values():
                            relative_path(path)
                    elif (set(value) != {"type", "path"} or value["type"] not in ("uri_file", "uri_folder")
                          or not isinstance(value["path"], str)
                          or not re.fullmatch(r"azureml:[A-Za-z0-9][A-Za-z0-9_.-]*:[0-9]+", value["path"])):
                        raise ExecutionError("Data bindings require a type and versioned workspace asset")
                elif not isinstance(value, (str, int, float, bool)):
                    raise ExecutionError("Input parameters must be JSON scalars")
            json.dumps(self.inputs, allow_nan=False)


@dataclass(frozen=True)
class RemoteJob:
    name: str
    status: str
    tags: dict[str, str]


class AzureGateway(Protocol):
    def submit(self, name: str, code: Path, payload: dict) -> RemoteJob: ...
    def get(self, name: str) -> RemoteJob | None: ...
    def download(self, name: str, destination: Path) -> Path: ...
    def cancel(self, name: str) -> None: ...


class AzureExecutor:
    backend = "azure_ml"

    def __init__(self, workspace: GitWorkspace, settings: AzureSettings,
                 journal: ServiceJournal, gateway: AzureGateway, *, live_authorized: bool = False):
        if live_authorized is not True:
            raise ExecutionError("Azure execution requires explicit resource authorization")
        if journal.service != "azure" or journal.unit != "gpu_seconds" or journal.path.parent != workspace.run_root:
            raise ExecutionError("Azure needs a GPU-second allowance in this run's service journal")
        self.workspace, self.settings, self.journal, self.gateway = workspace, settings, journal, gateway
        self.root = child_path(workspace.run_root, "azure")
        self.root.mkdir(exist_ok=True)

    @staticmethod
    def _request(request: JobRequest):
        if not re.fullmatch(r"EXP-[0-9]{3,}", request.experiment_id) or not re.fullmatch(r"[a-f0-9]{40}", request.git_commit):
            raise ExecutionError("Azure requires a controller-assigned experiment and exact commit")
        if request.experiment_id != f"EXP-{int(request.experiment_id[4:]):03d}":
            raise ExecutionError("Experiment identifiers must use canonical numbering")
        if ((request.experiment_id == "EXP-000") != (request.parent_experiment is None)
                or (request.parent_experiment is not None and
                    not re.fullmatch(r"EXP-[0-9]{3,}", request.parent_experiment))):
            raise ExecutionError("Invalid baseline or candidate parent identity")

    @staticmethod
    def _tags(payload: dict) -> dict[str, str]:
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return {"ri_intent_sha256": digest, "ri_commit": payload["request"]["git_commit"],
                "ri_experiment": payload["request"]["experiment_id"]}

    def _verify(self, intent, remote: RemoteJob | None) -> RemoteJob:
        if remote is None:
            raise ExecutionError("Submission unresolved; no matching remote job found. Never resubmit automatically")
        if remote.name != intent.name or any(remote.tags.get(k) != v for k, v in self._tags(intent.payload).items()):
            raise ExecutionError("Remote job does not match the durable submission intent")
        return remote

    def submit_job(self, request: JobRequest) -> str:
        self._request(request)
        # Separate service lock: callers may already own the outer run lock.
        with RunLock(self.root):
            existing = self.journal.get(request.experiment_id)
            if existing is not None:
                if existing.payload["request"] != asdict(request) or existing.payload["settings"] != asdict(self.settings):
                    raise ExecutionError("Existing Azure intent cannot be rebound")
                return self._reconcile(existing)
            files = self.workspace.verify_clean(request.git_commit)
            contract = load_contract(self.workspace.repository)
            if contract.evaluation_fingerprint is None:
                raise ExecutionError("Azure output needs a pinned evaluation protocol")
            if (contract.budget.max_gpu_hours is None
                    or self.journal.max_units > int(contract.budget.max_gpu_hours * 3600)):
                raise ExecutionError("The service allowance must fit the human-defined contract GPU budget")
            if int(request.experiment_id[4:]) > contract.budget.max_experiments:
                raise ExecutionError("The contract experiment allowance does not permit this candidate")
            job = read_yaml(child_path(self.workspace.repository, contract.execution.job_config))
            if not isinstance(job, dict) or job.get("type", "command") != "command" or not isinstance(job.get("command"), str):
                raise ExecutionError("The protected Azure definition must contain a command job")
            # This prepared-workload seam accepts only reviewed identity/data inputs.
            allowed_inputs = ({"config", "dataset", "weights"} if self.settings.inputs is None else set(self.settings.inputs)) | {"experiment_id", "parent_experiment", "source_commit"}
            if contract.execution.native != self.settings.native_job:
                raise ExecutionError("Execution mode differs from the approved contract")
            if not self.settings.native_job and (set(job.get("inputs", {})) != allowed_inputs or set(job.get("outputs", {})) != {"experiment_outputs"}):
                raise ExecutionError("Azure job inputs/outputs do not match the prepared workload interface")
            payload = {"request": asdict(request), "settings": asdict(self.settings),
                       "evaluation_fingerprint": contract.evaluation_fingerprint,
                       "command": job["command"], "environment_variables": job.get("environment_variables", {}),
                       "source": {name: entry.digest for name, entry in files.items() if entry.kind == "file"}}
            variables = payload["environment_variables"]
            if (not isinstance(variables, dict) or (not self.settings.native_job and set(variables) - {"PYTHONPATH", "CUBLAS_WORKSPACE_CONFIG", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE"})
                    or any(not isinstance(v, str) for v in variables.values())):
                raise ExecutionError("Only reviewed workload environment variables may be transmitted")
            if self.settings.native_job:
                from research_intern.execution.native import validate_native
                payload["native_job"] = validate_native(self.workspace.repository, contract.execution.job_config)
            code = child_path(self.root, request.experiment_id)
            if code.exists():
                raise ExecutionError("Unjournaled source staging exists; inspect rather than overwrite")
            with tempfile.TemporaryDirectory(dir=self.root, prefix="staging-") as temporary:
                stage = Path(temporary) / "code"
                source = stage / "source"
                source.mkdir(parents=True)
                for name, entry in files.items():
                    if entry.kind == "file":
                        target = child_path(source, name)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(child_path(self.workspace.repository, name).read_bytes())
                        target.chmod(entry.permissions)
                actual = {name: entry.digest for name, entry in snapshot_tree(source).items() if entry.kind == "file"}
                if actual != payload["source"]:
                    raise ExecutionError("Source changed while staging the exact candidate")
                if self.settings.native_job:
                    from research_intern.execution.native import stage_assets
                    stage_assets(self.workspace.run_root, source)
                self.workspace.verify_clean(request.git_commit)
                # Root ignore file prevents researcher .gitignore/.amlignore from
                # silently omitting source from Azure's code asset upload.
                (stage / ".amlignore").write_text(".git/\n", encoding="utf-8")
                (stage / "source_manifest.json").write_text(json.dumps(payload["source"], sort_keys=True), encoding="utf-8")
                stage.rename(code)
            intent, created = self.journal.reserve(request.experiment_id, payload,
                                                   units=self.settings.timeout_seconds * self.settings.gpu_count)
            if not created:
                return self._reconcile(intent)
            # Intent and reservation are committed before the first service call.
            # Every transport exception is ambiguous and leaves the intent intact.
            remote = self._verify(intent, self.gateway.submit(intent.name, code, payload))
            self.journal.attach(request.experiment_id, remote.name)
            return remote.name

    def _reconcile(self, intent) -> str:
        remote = self._verify(intent, self.gateway.get(intent.name))
        self.journal.attach(intent.request_id, remote.name)
        return remote.name

    def find_job(self, request: JobRequest) -> str | None:
        """Reconcile only; never submit on an ambiguous controller restart."""
        intent = self.journal.get(request.experiment_id)
        if intent is None:
            return None
        if intent.payload["request"] != asdict(request):
            raise ExecutionError("Saved Azure request differs from the ledger")
        return self._reconcile(intent)

    def _owned(self, job_id: str):
        if not re.fullmatch(r"ri-azure-[a-f0-9]{32}", job_id):
            raise ExecutionError("Only controller-owned Azure jobs may be accessed")
        intent = self.journal.find_name(job_id)
        if intent is None:
            raise ExecutionError("Unknown Azure job")
        return intent, self._verify(intent, self.gateway.get(job_id))

    def get_status(self, job_id: str) -> JobStatus:
        _, remote = self._owned(job_id)
        status = {"NotStarted": "queued", "Starting": "queued", "Provisioning": "queued",
                  "Preparing": "queued", "Queued": "queued", "Running": "running",
                  "Finalizing": "running", "CancelRequested": "running", "Completed": "completed",
                  "Failed": "failed", "Canceled": "cancelled"}.get(remote.status)
        if status is None:
            raise ExecutionError("Unrecognized Azure job status; retain evidence for reconciliation")
        return status

    def cancel_job(self, job_id: str) -> None:
        _, remote = self._owned(job_id)
        if remote.status not in {"Completed", "Failed", "Canceled"}:
            self.gateway.cancel(job_id)

    def download_outputs(self, job_id: str, destination: Path) -> None:
        intent, remote = self._owned(job_id)
        if remote.status not in {"Completed", "Failed", "Canceled"}:
            raise ExecutionError("Only terminal Azure jobs may be collected")
        contract = load_contract(self.workspace.repository)
        expected = child_path(self.workspace.run_root, "experiments", intent.request_id, contract.outputs.root)
        if destination.absolute() != expected:
            raise ExecutionError("Outputs must use this experiment's owned output directory")
        expected.parent.mkdir(parents=True, exist_ok=True)
        with RunLock(self.root):
            if expected.exists():
                raise ExecutionError("Existing experiment outputs cannot be overwritten")
            with tempfile.TemporaryDirectory(dir=self.root, prefix="download-") as temporary:
                bundle = self.gateway.download(job_id, Path(temporary))
                if not bundle.resolve().is_relative_to(Path(temporary).resolve()):
                    raise ExecutionError("Downloaded outputs escaped their staging directory")
                validate_tree(bundle)
                bundle.rename(expected)


class AzureMLGateway:
    """Azure SDK implementation; construction never initiates interactive login.

    Environment registration/build, compute sizing and data access are human setup.
    A pinned image environment must not have a mutable build or conda overlay.
    """

    def __init__(self, settings: AzureSettings, *, live_authorized: bool = False):
        if live_authorized is not True:
            raise ExecutionError("Azure access requires explicit authorization")
        from azure.ai.ml import MLClient
        from azure.core.pipeline.transport import RequestsTransport
        from research_intern.execution.identity import credential_for
        self.settings = settings
        self.credential = credential_for(settings.subscription_id)
        self.transport = RequestsTransport(connection_timeout=15, read_timeout=60)
        self.client = MLClient(self.credential, settings.subscription_id, settings.resource_group,
                               settings.workspace_name, retry_total=0, transport=self.transport,
                               show_progress=False, enable_telemetry=False)

    @staticmethod
    def _remote(job) -> RemoteJob:
        return RemoteJob(job.name, job.status, job.tags or {})

    def get(self, name: str) -> RemoteJob | None:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return self._remote(self.client.jobs.get(name))
        except ResourceNotFoundError:
            return None  # All other exceptions remain ambiguous, never "not found".

    def preflight(self) -> dict:
        """Read resource metadata; never create assets, run compute or sign in."""
        settings = self.settings
        environment = None
        if not settings.native_job:
            _, name, version = settings.environment.split(":")
            environment = self.client.environments.get(name, version)
            if environment.image != settings.image or environment.conda_file or environment.build:
                raise ExecutionError("Azure environment differs from the approved immutable image")
        compute = self.client.compute.get(settings.compute)
        # Azure SDK versions return either a string or a string-valued Enum.
        # str(ProvisioningState.SUCCEEDED) is not the wire value "Succeeded".
        state = compute.provisioning_state
        if str(getattr(state, "value", state)).lower() != "succeeded":
            raise ExecutionError("Approved compute is not provisioned")
        sizes = self.client.compute.list_sizes(location=compute.location)
        size = next((s for s in sizes if s.name.casefold() == compute.size.casefold()), None)
        if size is None or size.gpus != settings.gpu_count:
            raise ExecutionError("Compute GPU count differs from the reserved allowance")
        bindings = settings.inputs if settings.inputs is not None else {
            "dataset": {"type": "uri_folder", "path": settings.dataset},
            "weights": {"type": "uri_file", "path": settings.weights}}
        for binding in bindings.values():
            if isinstance(binding, dict) and "path" in binding:
                _, name, version = binding["path"].split(":")
                data = self.client.data.get(name, version)
                if data.type != binding["type"]:
                    raise ExecutionError("Versioned data asset has the wrong type")
        return {"compute_size": compute.size, "gpus": size.gpus, "image": environment.image if environment else None}

    def submit(self, name: str, code: Path, payload: dict) -> RemoteJob:
        from azure.ai.ml import Input, Output, command
        settings = self.settings
        if payload["settings"] != asdict(settings):
            raise ExecutionError("The Azure client configuration differs from the saved intent")
        if settings.native_job:
            from research_intern.execution.native import submit_native
            return self._remote(submit_native(self.client, name, code, payload, AzureExecutor._tags(payload)))
        _, environment_name, version = settings.environment.split(":")
        environment = self.client.environments.get(environment_name, version)
        if environment.image != settings.image or environment.conda_file or environment.build:
            raise ExecutionError("Azure environment differs from the approved immutable image")
        request = payload["request"]
        bindings = settings.inputs if settings.inputs is not None else {
            "config": {"baseline": "configs/baseline.yaml", "candidate": "configs/candidate.yaml"},
            "dataset": {"type": "uri_folder", "path": settings.dataset},
            "weights": {"type": "uri_file", "path": settings.weights},
        }
        inputs = {}
        for key, value in bindings.items():
            if isinstance(value, dict) and "baseline" in value:
                inputs[key] = value["baseline" if request["experiment_id"] == "EXP-000" else "candidate"]
            elif isinstance(value, dict):
                inputs[key] = Input(type=value["type"], path=value["path"], mode="ro_mount")
            else:
                inputs[key] = value
        inputs.update(experiment_id=request["experiment_id"], parent_experiment=request["parent_experiment"] or "none",
                      source_commit=request["git_commit"])
        job = command(
            name=name, code=str(code), command="cd source && " + payload["command"],
            environment=settings.environment, compute=settings.compute,
            instance_count=1, limits={"timeout": settings.timeout_seconds},
            environment_variables=payload["environment_variables"],
            inputs=inputs,
            outputs={"experiment_outputs": Output(type="uri_folder", mode="rw_mount")},
            tags=AzureExecutor._tags(payload),
        )
        return self._remote(self.client.jobs.create_or_update(job))

    def download(self, name: str, destination: Path) -> Path:
        output = "ri_results" if self.settings.native_job else "experiment_outputs"
        self.client.jobs.download(name=name, output_name=output, download_path=str(destination))
        return destination / "named-outputs" / output

    def cancel(self, name: str) -> None:
        # This requests cancellation; only later polling confirms terminal status.
        self.client.jobs.begin_cancel(name, polling=False)

    def close(self) -> None:
        try:
            self.transport.close()
        finally:
            self.credential.close()
