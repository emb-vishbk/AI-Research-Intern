"""Composition of one approved live project; no service calls during inspection.

Configuration is a local operator action, distinct from uploading untrusted source.
Baselines are measured by the same execution/scoring path as later candidates.
"""
from __future__ import annotations

import asyncio
import json
import math
import hashlib
from datetime import UTC, datetime, timedelta
from dataclasses import asdict
from pathlib import Path

from research_intern.contracts.loader import load_contract, read_yaml
from research_intern.controller.candidate import CandidateController
from research_intern.controller.execution import ExecutionController
from research_intern.controller.loop import ResearchLoop
from research_intern.domain.experiments import SliceError, is_finite_number
from research_intern.domain.research import continuation_reasons
from research_intern.evaluation.trusted import TrustedScorer, freeze_scorer, inventory
from research_intern.execution.azure import AzureExecutor, AzureMLGateway, AzureSettings
from research_intern.execution.outputs import read_json
from research_intern.ledger.preparations import PreparationJournal
from research_intern.ledger.services import ServiceJournal
from research_intern.ledger.sqlite import Ledger
from research_intern.workspace.git import GitWorkspace
from research_intern.workspace.importing import contract_digest
from research_intern.workspace.lock import RunLock
from research_intern.workspace.paths import child_path
from research_intern.workspace.project import ProjectStore
from research_intern.workspace.recovery import atomic_bytes


def validate_settings(value: dict, workspace: Path) -> dict:
    required = {"azure", "copilot", "scoring"}
    if not isinstance(value, dict) or set(value) != required:
        raise SliceError("Live settings require exactly azure, copilot and scoring sections")
    if any(not isinstance(value[key], dict) for key in required):
        raise SliceError("Each live settings section must be an object")
    azure = dict(value["azure"])
    if "inputs" in azure:
        azure.setdefault("dataset", "")
        azure.setdefault("weights", "")
    try:
        settings = AzureSettings(**azure)
    except (TypeError, ValueError) as exc:
        raise SliceError("Invalid Azure settings; check required names, bindings and numeric limits") from exc
    agent, scorer = value["copilot"], value["scoring"]
    if set(agent) != {"runtime_path", "model", "readable_paths", "max_turns", "credits_per_turn", "timeout_seconds"}:
        raise SliceError("Declare runtime, model, readable_paths, max_turns, credits_per_turn and timeout_seconds")
    if not isinstance(agent["runtime_path"], str):
        raise SliceError("Copilot runtime_path must be a workspace-relative string")
    runtime = child_path(workspace, agent["runtime_path"])
    if not runtime.is_file():
        raise SliceError("Provision the Copilot runtime at the configured workspace-relative path first")
    if type(agent["max_turns"]) is not int or not 1 <= agent["max_turns"] <= 1000:
        raise SliceError("Choose a coding turn limit from 1 to 1000")
    if not is_finite_number(agent["credits_per_turn"]) or agent["credits_per_turn"] <= 0:
        raise SliceError("Choose a positive per-turn AI credit ceiling")
    if not isinstance(agent["model"], str) or not agent["model"].strip():
        raise SliceError("Choose an explicit Copilot model")
    if type(agent["timeout_seconds"]) is not int or not 1 <= agent["timeout_seconds"] <= 600:
        raise SliceError("Coding timeout must be 1–600 seconds")
    if not isinstance(agent["readable_paths"], list) or not agent["readable_paths"]:
        raise SliceError("Review an explicit source read allowlist")
    if set(scorer) != {"python", "files", "timeout_seconds"} or not isinstance(scorer["files"], list):
        raise SliceError("Scoring needs python, protected files and timeout_seconds")
    if not isinstance(scorer["python"], str) or any(not isinstance(p, str) for p in scorer["files"] + agent["readable_paths"]):
        raise SliceError("Scoring and source paths must be strings")
    python = Path(scorer["python"])
    if not python.is_absolute() or not python.is_file():
        raise SliceError("Scoring python must be an absolute path to an installed interpreter")
    if type(scorer["timeout_seconds"]) is not int or not 1 <= scorer["timeout_seconds"] <= 3600:
        raise SliceError("Scoring timeout must be 1–3600 seconds")
    return {"azure": asdict(settings), "copilot": agent, "scoring": scorer}


class LiveProject:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve(strict=True)
        self.projects = ProjectStore(self.workspace)
        self.root = self.projects.root()

    def configure(self, value: dict, *, authorized: bool = False) -> dict:
        if authorized is not True:
            raise SliceError("Configuring live services needs explicit authorization")
        if not self.root.is_dir():
            raise SliceError("Upload and prepare a project first")
        with RunLock(self.root):
            project = self.projects.inspect()
            if not project["workspace_prepared"] or project["evaluation_status"] != "confirmed":
                raise SliceError("Prepare source and confirm its evaluation protocol before live configuration")
            settings = validate_settings(value, self.workspace)
            repository = child_path(self.root, "repository")
            contract = load_contract(repository)
            from research_intern.copilot.files import ControlledFiles
            ControlledFiles(repository, contract, tuple(settings["copilot"]["readable_paths"]))
            if project["budget"] != asdict(contract.budget):
                raise SliceError("Saved budget must match the prepared contract; finalize policy before preparation")
            if (contract.budget.max_experiments <= 0 or not contract.budget.max_gpu_hours
                    or not contract.budget.max_ai_credits):
                raise SliceError("Approve positive experiment, GPU and AI-credit allowances in the contract")
            if settings["copilot"]["credits_per_turn"] > contract.budget.max_ai_credits:
                raise SliceError("One coding turn exceeds the total AI-credit allowance")
            if settings["azure"]["timeout_seconds"] * settings["azure"]["gpu_count"] > contract.budget.max_gpu_hours * 3600:
                raise SliceError("The baseline's GPU reservation exceeds the total allowance")
            job = read_yaml(repository / contract.execution.job_config)
            bindings = settings["azure"]["inputs"]
            expected = ({"config", "dataset", "weights"} if bindings is None else set(bindings)) | {"experiment_id", "parent_experiment", "source_commit"}
            if contract.execution.native != settings["azure"]["native_job"]:
                raise SliceError("Execution mode must match the approved project")
            if not contract.execution.native and (set(job.get("inputs", {})) != expected or set(job.get("outputs", {})) != {"experiment_outputs"}):
                raise SliceError("Protected job inputs/outputs do not match the configured live bindings")
            local_bindings = bindings if bindings is not None else {"config": {
                "baseline": "configs/baseline.yaml", "candidate": "configs/candidate.yaml"}}
            for binding in local_bindings.values():
                if isinstance(binding, dict) and "baseline" in binding:
                    for name in binding.values():
                        if not child_path(repository, name).is_file():
                            raise SliceError(f"Configured workload file is missing: {name}")
            record = {"version": 1, "source_commit": project["source_commit"],
                      "contract_sha256": project["contract_sha256"],
                      "evaluation_fingerprint": project["evaluation_fingerprint"], "settings": settings}
            config = child_path(self.root, "live.json")
            if config.exists():
                previous = read_json(config)
                if any(previous.get(k) != v for k,v in record.items()):
                    raise SliceError("Live configuration is fixed; do not change a run's approved services")
                return previous
            record["scorer_files"] = freeze_scorer(repository, self.root / "trusted-scorer", contract, settings["scoring"]["files"])
            TrustedScorer(self.root, contract, python=settings["scoring"]["python"], files=record["scorer_files"],
                          timeout_seconds=settings["scoring"]["timeout_seconds"])
            atomic_bytes(config, json.dumps(record, indent=2, allow_nan=False).encode())
            return record

    def configuration(self) -> dict:
        config = read_json(child_path(self.root, "live.json"))
        repository = child_path(self.root, "repository")
        if contract_digest(repository) != config["contract_sha256"]:
            raise SliceError("Approved live contract changed")
        confirmation = read_json(child_path(self.root, "evaluation.json"))
        expected = {"source_commit": config["source_commit"], "contract_sha256": config["contract_sha256"],
                    "evaluation_fingerprint": config["evaluation_fingerprint"], "confirmed": True}
        if confirmation != expected:
            raise SliceError("Evaluation confirmation differs from live configuration")
        return config

    async def verify_services(self) -> dict:
        """Explicit read-only remote checks; no model turn or GPU submission."""
        with RunLock(self.root):
            config = self.configuration()
            try:
                gateway = AzureMLGateway(AzureSettings(**config["settings"]["azure"]), live_authorized=True)
                try:
                    azure = gateway.preflight()
                finally:
                    gateway.close()
                from research_intern.copilot.live import check_authentication
                await check_authentication(self.workspace, child_path(self.workspace, config["settings"]["copilot"]["runtime_path"]),
                                           model=config["settings"]["copilot"]["model"])
            except SliceError:
                raise
            except Exception as exc:
                # Provider errors can contain credential/request details. Keep them
                # out of browser responses while identifying the failing boundary.
                raise SliceError(f"Service verification failed ({type(exc).__name__}); check local authentication and resource access") from exc
            proof = {"settings_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                     "checked_at": datetime.now(UTC).isoformat(), "azure": azure, "copilot_authenticated": True}
            atomic_bytes(self.root / "services-verified.json", json.dumps(proof).encode())
            return proof

    def invalidate_service_check(self):
        """An account change requires a fresh remote check, not a new experiment."""
        if not self.root.is_dir():
            return
        with RunLock(self.root):
            path = child_path(self.root, "services-verified.json")
            if path.is_file():
                try:
                    proof = read_json(path)
                except (SliceError, ValueError):
                    # Inspection already blocks a malformed receipt. Preserve it
                    # for diagnosis and keep the dashboard available for recovery.
                    return
                if not isinstance(proof, dict):
                    return
                proof["invalidated"] = True
                atomic_bytes(path, json.dumps(proof).encode())

    def initialize(self) -> None:
        with RunLock(self.root):
            config = self.configuration()
            repository = child_path(self.root, "repository")
            contract = load_contract(repository)
            from research_intern.execution.imported import freeze_existing
            freeze_existing(self.root, self.workspace, config)
            # No partial initialization can silently become a different baseline.
            GitWorkspace(self.root, repository).verify_clean(config["source_commit"])
            with Ledger(self.root, contract.rules, contract=contract, repository=repository, mode="live") as ledger:
                PreparationJournal(ledger).configure(max_attempts=config["settings"]["copilot"]["max_turns"], scenario="live")
                self.journals(config)
                ledger.reserve_baseline(config["source_commit"])
                ledger._db.execute("INSERT OR IGNORE INTO metadata VALUES ('live_phase', 'baseline')")

    def activate(self):
        with RunLock(self.root), Ledger.reopen(self.root) as ledger:
            state = self.inspect()
            if not state["baseline_accepted"] or state.get("continuation_blockers"):
                raise SliceError("The baseline must be accepted and research must have remaining allowances")
            ledger._db.execute("INSERT OR REPLACE INTO metadata VALUES ('live_phase', 'research')")

    def journals(self, config):
        contract = load_contract(child_path(self.root, "repository"))
        return (
            ServiceJournal(self.root, service="azure", unit="gpu_seconds", max_units=math.floor(contract.budget.max_gpu_hours * 3600)),
            ServiceJournal(self.root, service="copilot", unit="turns", max_units=config["settings"]["copilot"]["max_turns"]),
            ServiceJournal(self.root, service="copilot_credits", unit="microcredits", max_units=math.floor(contract.budget.max_ai_credits * 1_000_000)),
        )

    def inspect(self) -> dict:
        result = {"configured": False, "services_verified": False, "initialized": False, "baseline_accepted": False,
                  "error": None, "usage": None, "continuation_blockers": []}
        if not self.root.is_dir() or not (self.root / "live.json").is_file():
            return result
        try:
            config = self.configuration()
            if inventory(self.root / "trusted-scorer") != config["scorer_files"]:
                raise SliceError("Frozen scorer changed")
            result["configured"] = True
            proof_path = self.root / "services-verified.json"
            if proof_path.is_file():
                proof = read_json(proof_path)
                result["services_verified"] = (
                    not proof.get("invalidated", False)
                    and proof.get("settings_sha256") == hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
                    and proof.get("copilot_authenticated") is True
                    and timedelta(0) <= datetime.now(UTC) - datetime.fromisoformat(proof["checked_at"]) < timedelta(hours=24))
            if (self.root / "ledger.sqlite3").is_file():
                with Ledger.reopen(self.root) as ledger:
                    if ledger.mode != "live":
                        raise SliceError("The project ledger is not a live ledger")
                    baseline = next((r for r in ledger.history() if r.sequence == 0), None)
                    result["initialized"] = baseline is not None
                    result["baseline_accepted"] = bool(baseline and baseline.state == "RECORDED" and baseline.decision in ("KEEP", "GOAL_REACHED"))
                    result["baseline_state"] = baseline.state if baseline else None
                    result["stopped"] = ledger.snapshot().stop_requested
                    result["continuation_blockers"] = [reason for reason in continuation_reasons(ledger.snapshot()) if reason != "BASELINE_MISSING"]
                    journal = PreparationJournal(ledger)
                    if journal.count() >= config["settings"]["copilot"]["max_turns"]:
                        result["continuation_blockers"].append("PREPARATION_BUDGET_EXHAUSTED")
                azure, turns, credits = self.journals(config)
                result["usage"] = {"azure": azure.usage(), "copilot": turns.usage(), "credits": credits.usage()}
                settings, agent = config["settings"]["azure"], config["settings"]["copilot"]
                for remaining, needed, reason in (
                    (azure.usage()["remaining"], settings["gpu_count"] * settings["timeout_seconds"], "GPU_BUDGET_EXHAUSTED"),
                    (turns.usage()["remaining"], 1, "CODING_TURN_BUDGET_EXHAUSTED"),
                    (credits.usage()["remaining"], math.ceil(agent["credits_per_turn"] * 1_000_000), "AI_CREDIT_BUDGET_EXHAUSTED"),
                ):
                    if remaining < needed:
                        result["continuation_blockers"].append(reason)
        except (SliceError, ValueError, OSError, KeyError, TypeError) as exc:
            result.update(configured=False, error=str(exc))
        return result

    async def run(self, *, baseline_only=None, steps=None, emit=print, gateway=None, proposer_factory=None):
        config = self.configuration()
        settings, agent, scoring = (config["settings"][key] for key in ("azure", "copilot", "scoring"))
        azure, turns, credits = self.journals(config)
        owned_gateway = gateway is None
        if owned_gateway and not self.inspect()["services_verified"]:
            raise SliceError("Verify Azure resources and Copilot authentication before live execution (checks expire after 24 hours)")
        gateway = gateway or AzureMLGateway(AzureSettings(**settings), live_authorized=True)
        try:
            with Ledger.reopen(self.root) as ledger:
                phase = ledger._db.execute("SELECT value FROM metadata WHERE key='live_phase'").fetchone()
                baseline_only = baseline_only is True or not phase or phase[0] != "research"
                workspace = GitWorkspace(self.root, ledger.repository)
                executor = AzureExecutor(workspace, AzureSettings(**settings), azure, gateway, live_authorized=True)
                if (self.root / "imported-baseline.json").is_file():
                    from research_intern.execution.imported import ImportedBaselineExecutor
                    executor = ImportedBaselineExecutor(self.root, executor)
                scorer = TrustedScorer(self.root, ledger.contract, python=scoring["python"], files=config["scorer_files"], timeout_seconds=scoring["timeout_seconds"])
                if proposer_factory is None:
                    from research_intern.copilot.live import CopilotProposer
                    proposer_factory = lambda: CopilotProposer(workspace=self.workspace,
                        runtime_path=child_path(self.workspace, agent["runtime_path"]), model=agent["model"],
                        readable_paths=tuple(agent["readable_paths"]), timeout_seconds=agent["timeout_seconds"],
                        journal=turns, credit_journal=credits, credits_per_turn=agent["credits_per_turn"],
                        live_authorized=True, emit=emit)
                candidates = CandidateController(ledger, workspace, proposer_factory, timeout_seconds=agent["timeout_seconds"] + 25)
                def admission():
                    reasons = []
                    if azure.usage()["remaining"] < settings["timeout_seconds"] * settings["gpu_count"]:
                        reasons.append("GPU_BUDGET_EXHAUSTED")
                    if turns.usage()["remaining"] < 1:
                        reasons.append("CODING_TURN_BUDGET_EXHAUSTED")
                    if credits.usage()["remaining"] < math.ceil(agent["credits_per_turn"] * 1_000_000):
                        reasons.append("AI_CREDIT_BUDGET_EXHAUSTED")
                    return reasons
                return await ResearchLoop(candidates, ExecutionController(ledger, executor, verifier=scorer),
                    admission=admission, baseline_only=baseline_only, emit=emit).run(steps=steps, poll_interval=10 if owned_gateway else 0)
        finally:
            if owned_gateway:
                gateway.close()
