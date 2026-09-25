"""Local mission control services; HTTP and browser concerns live elsewhere."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from research_intern.controller.loop import describe_run
from research_intern.controller.readiness import project_readiness
from research_intern.domain.experiments import SliceError
from research_intern.execution.outputs import read_json
from research_intern.ledger.preparations import PreparationJournal
from research_intern.ledger.sqlite import Ledger
from research_intern.offline import compose_loop, initialize_run
from research_intern.live import LiveProject
from research_intern.workspace.paths import child_path
from research_intern.workspace.project import LoopBudget, ProjectStore

log = logging.getLogger(__name__)
RUN_ID = re.compile(r"loop-[A-Za-z0-9_-]{1,64}\Z")


class MissionBusy(SliceError):
    """An operation conflicts with the server's serial driver."""


class RunNotFound(SliceError):
    """The requested run or experiment is absent."""


class MissionControl:
    """One background thread drives the existing loop with its own SQLite connection.

    Request reads open independent connections. The loop's OS lock remains the
    authority for ownership of each run, including when using the CLI separately.
    """

    def __init__(self, workspace: Path, *, poll_interval: float = 0.5):
        self.workspace = workspace.resolve(strict=True)
        self.poll_interval = poll_interval
        self._guard = threading.Lock()
        self._active: str | None = None
        self._creating = False
        self._project_busy = False
        self._closing = False
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._errors: dict[str, str] = {}
        self.projects = ProjectStore(self.workspace)
        self.live = LiveProject(self.workspace)
        self._baseline_only = False

    def activity(self) -> dict:
        with self._guard:
            return {"active_run_id": self._active, "creating": self._creating}

    def directory(self, run_id: str) -> Path:
        if run_id == "live-project":
            root = self.live.root
            if not root.is_dir() or not child_path(root, "ledger.sqlite3").is_file():
                raise RunNotFound("The live baseline has not been initialized")
            return root
        if not RUN_ID.fullmatch(run_id):
            raise RunNotFound("Unknown offline run")
        root = child_path(self.workspace, ".runtime", "simulations", run_id)
        if not root.is_dir() or not child_path(root, "ledger.sqlite3").is_file():
            raise RunNotFound("Unknown offline run")
        return root

    def project(self) -> dict:
        return self.projects.inspect()

    @contextmanager
    def project_operation(self):
        """Keep uploads/settings and the serial controller mutually exclusive."""
        with self._guard:
            if self._active or self._creating or self._closing or self._project_busy:
                raise MissionBusy("Wait for the current operation to finish before changing the project")
            self._project_busy = True
        try:
            yield
        finally:
            with self._guard:
                self._project_busy = False

    def save_budget(self, budget: LoopBudget, contract_sha256: str) -> dict:
        with self.project_operation():
            return self.projects.save_budget(budget, contract_sha256)

    def prepare_project(self, contract_sha256: str) -> dict:
        with self.project_operation():
            return self.projects.prepare(contract_sha256)

    def confirm_evaluation(self, contract_sha256: str, source_commit: str, evaluation_fingerprint: str) -> dict:
        with self.project_operation():
            return self.projects.confirm_evaluation(contract_sha256, source_commit, evaluation_fingerprint)

    def dashboard(self) -> dict:
        """One workspace view; old simulated evidence never becomes a real baseline."""
        project = self.project()
        with self._guard:
            active, changing = self._active, self._project_busy or self._creating
        run = None
        history_error = None
        live = self.live.inspect()
        try:
            if active:
                run = self.status(active)
            elif live["initialized"]:
                run = self.status("live-project")
            elif project["status"] == "missing":
                root = child_path(self.workspace, ".runtime", "simulations")
                directories = sorted((p for p in root.iterdir() if RUN_ID.fullmatch(p.name)),
                                     key=lambda p: p.lstat().st_mtime, reverse=True) if root.exists() else []
                if directories:
                    run = self.status(directories[0].name)
        except (SliceError, OSError, ValueError, sqlite3.Error) as exc:
            history_error = str(exc)
        readiness = project_readiness(project, live)
        return {"project": project, "run": run, "changing": changing, "history_error": history_error,
            "live": live, "can_measure_baseline": live["configured"] and live["services_verified"] and not live["initialized"],
            "can_start": readiness["can_start"], "readiness": readiness,
            "start_blocker": "Live research is blocked: " + " ".join(item["message"] for item in readiness["blockers"])}

    def start_project(self) -> dict:
        status = self.dashboard()
        if not status["can_start"]:
            raise MissionBusy(status["start_blocker"])
        self.live.activate()
        return self.start("live-project")

    def configure_live(self, settings: dict, *, authorized=False) -> dict:
        with self.project_operation():
            self.live.configure(settings, authorized=authorized)
        return self.dashboard()

    def measure_baseline(self) -> dict:
        with self.project_operation():
            if not self.live.inspect()["services_verified"]:
                raise MissionBusy("Verify live services before submitting the baseline")
            self.live.initialize()
        return self.start("live-project", baseline_only=True)

    def verify_live(self):
        with self.project_operation():
            asyncio.run(self.live.verify_services())
        return self.dashboard()

    def create(self, *, max_experiments: int = 4, max_attempts: int | None = None,
               scenario: str = "mixed") -> dict:
        with self._guard:
            if self._active or self._creating or self._closing or self._project_busy:
                raise MissionBusy("Wait for the active operation before creating another run")
            self._creating = True
        try:
            root = initialize_run(self.workspace, max_experiments=max_experiments,
                                  max_attempts=max_attempts, scenario=scenario)
            return self.status(root.name)
        finally:
            with self._guard:
                self._creating = False

    def runs(self) -> dict:
        root = child_path(self.workspace, ".runtime", "simulations")
        directories = sorted((p for p in root.iterdir() if RUN_ID.fullmatch(p.name)),
                             key=lambda p: p.lstat().st_mtime, reverse=True) if root.exists() else []
        rows = []
        if self.live.inspect()["initialized"]:
            status = self.status("live-project")
            rows.append({key: status[key] for key in ("id", "controller", "research_state", "driver")})
        for directory in directories[:50]:
            try:
                status = self.status(directory.name)
                rows.append({key: status[key] for key in ("id", "controller", "research_state", "driver")})
            except (SliceError, OSError, ValueError, sqlite3.Error) as exc:
                rows.append({"id": directory.name, "error": str(exc)})
        return {**self.activity(), "runs": rows, "truncated": len(directories) > 50}

    def status(self, run_id: str) -> dict:
        with Ledger.reopen(self.directory(run_id)) as ledger:
            result = describe_run(ledger)
            records = ledger.history()
            # Diffs and full metrics belong in the selected experiment detail.
            experiments = []
            for record in records:
                experiments.append({"experiment_id": record.experiment_id,
                                    "parent_experiment": record.parent_experiment,
                                    "state": record.state, "decision": record.decision,
                                    "score": record.evaluation.score if record.evaluation else None,
                                    "hypothesis": record.hypothesis,
                                    "planned_intervention": record.planned_intervention,
                                    "git_commit": record.git_commit})
            journal = PreparationJournal(ledger)
            latest = journal.latest()
            result.update(id=run_id, experiments=experiments, events=ledger.recent_events(),
                          policy=journal.policy(), preparation_failures=journal.failures(),
                          current_plan=latest.checkpoint.get("plan") if latest else None)
            if ledger.mode == "live":
                result["service_usage"] = self.live.inspect()["usage"]
                best = result["research_state"]["best_experiment"]
                result["summary"] = {"best": best, "baseline": result["research_state"]["baseline"],
                                     "experiments": len(records), "mode": "live"}
        with self._guard:
            result["driver"] = {"active": self._active == run_id, "active_run_id": self._active,
                                "error": self._errors.get(run_id)}
        return result

    def experiment(self, run_id: str, experiment_id: str) -> dict:
        with Ledger.reopen(self.directory(run_id)) as ledger:
            record = next((r for r in ledger.history() if r.experiment_id == experiment_id), None)
            if record is None:
                raise RunNotFound("Unknown experiment")
            result = asdict(record)
            # Candidate evidence is published after the ledger reservation. A
            # crash in that gap leaves a valid record; resume republishes it.
            evidence = child_path(ledger.root, "experiments", experiment_id, "candidate.json")
            result["candidate"] = read_json(evidence) if evidence.is_file() else None
            receipt = child_path(ledger.root, "experiments", experiment_id, "verified-score.json")
            result["verified_score"] = read_json(receipt) if receipt.is_file() else None
            return result

    def report(self, run_id: str) -> dict:
        """Export scientific lineage and scored evidence, without credentials."""
        result = self.status(run_id)
        result["report_version"] = 1
        result["experiments"] = [self.experiment(run_id, row["experiment_id"]) for row in result["experiments"]]
        if run_id == "live-project":
            config = self.live.configuration()
            result["provenance"] = {key: config[key] for key in (
                "source_commit", "contract_sha256", "evaluation_fingerprint", "scorer_files")}
            imported = self.live.root / "imported-baseline.json"
            if imported.is_file():
                result["provenance"]["imported_baseline"] = read_json(imported)
        return result

    def start(self, run_id: str, *, baseline_only=False) -> dict:
        self.status(run_id)  # Validate before scheduling any work.
        with self._guard:
            if self._active or self._creating or self._closing or self._project_busy:
                raise MissionBusy("One research run can be driven at a time")
            self._active = run_id
            self._baseline_only = baseline_only
            self._errors.pop(run_id, None)
            self._thread = threading.Thread(target=self._drive, args=(run_id,),
                                            name=f"research-{run_id}", daemon=False)
            try:
                self._thread.start()
            except RuntimeError:
                self._active = None
                raise
        return {"id": run_id, "accepted": True}

    def stop(self, run_id: str) -> dict:
        with Ledger.reopen(self.directory(run_id)) as ledger:
            describe_run(ledger)
            ledger.request_stop()
        return self.status(run_id)

    def _drive(self, run_id: str) -> None:
        async def drive() -> None:
            with self._guard:
                self._loop = asyncio.get_running_loop()
                self._task = asyncio.current_task()
                if self._closing:
                    return
            if run_id == "live-project":
                await self.live.run(baseline_only=self._baseline_only, emit=log.info)
            else:
                with Ledger.reopen(self.directory(run_id)) as ledger:
                    await compose_loop(ledger, emit=log.info).run(poll_interval=self.poll_interval)

        try:
            asyncio.run(drive())
        except asyncio.CancelledError:
            pass  # ResearchLoop persisted INTERRUPTED; shutdown is not human stop.
        except Exception as exc:
            log.exception("Research driver failed for %s", run_id)
            with self._guard:
                self._errors[run_id] = str(exc) or type(exc).__name__
        finally:
            with self._guard:
                self._active = None
                self._loop = None
                self._task = None

    def close(self) -> None:
        with self._guard:
            self._closing = True
            thread = self._thread
            if self._loop is not None and self._task is not None:
                try:
                    self._loop.call_soon_threadsafe(self._task.cancel)
                except RuntimeError:
                    # asyncio.run may have closed its loop just before the
                    # thread acquires this guard to publish its final state.
                    if not self._loop.is_closed():
                        raise
        if thread is not None:
            thread.join(timeout=35)
            if thread.is_alive():
                raise MissionBusy("The research driver is still shutting down; inspect the server log")
