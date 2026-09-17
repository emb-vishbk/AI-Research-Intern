"""Offline contract, actual filesystem enforcement, Git lineage, and integration tests."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from research_intern.candidate_demo import prepare_candidate_fixture
from research_intern.contracts.loader import load_contract, read_yaml
from research_intern.contracts.models import ContractError, ExperimentContract, OutputPaths
from research_intern.controller.candidate import CandidateController
from research_intern.controller.execution import ExecutionController
from research_intern.controller.handoff import HandoffBlocked
from research_intern.copilot.editing import ScriptedEditor
from research_intern.domain.experiments import JobRequest, SliceError
from research_intern.domain.research import CandidatePlan
from research_intern.execution.simulated import SimulatedExecutor, write_outputs
from research_intern.ledger.research_state import build_research_state
from research_intern.ledger.sqlite import Ledger, LedgerError
from research_intern.main import main
from research_intern.validation.preflight import PreflightError, validate_files
from research_intern.workspace.git import GitWorkspace, PermissionViolation, WorkspaceError

from test_execution_slice import WORKSPACE, WorkspaceTest

MINIMAL = {
    "version": "1.0", "objective": {"metric": "f1", "direction": "maximize"},
    "execution": {"backend": "azure_ml", "job_config": "job.yaml"},
    "outputs": {"root": "experiment_outputs/"},
    "scope": {"editable": ["model.py", "configs/"], "protected": ["evaluate.py", "data/test/"]},
    "budget": {"max_experiments": 3},
}


class ContractTests(unittest.TestCase):
    def test_minimal_contract_defaults_and_canonical_round_trip(self):
        contract = ExperimentContract.from_dict(MINIMAL)
        self.assertEqual(contract.outputs, OutputPaths())
        self.assertIsNone(contract.budget.max_gpu_hours)
        self.assertEqual(ExperimentContract.from_dict(contract.to_dict()), contract)
        self.assertTrue(contract.allows("configs/nested/aug.yaml"))
        self.assertFalse(contract.allows("configs-extra/aug.yaml"))
        self.assertFalse(contract.allows("configs/.gitattributes"))

    def test_objectives_constraints_and_budgets_are_strict(self):
        examples = [
            ("version", None, 1.0), ("objective", "direction", "sideways"),
            ("objective", "metric", " "), ("objective", "target", True),
            ("objective", "target", float("nan")), ("budget", "max_experiments", True),
            ("objective", "target", None),
            ("budget", "max_experiments", -1), ("budget", "max_experiments", 1.5),
            ("budget", "max_gpu_hours", float("inf")), ("budget", "max_ai_credits", -1),
            ("execution", "backend", "simulated"), ("execution", "smoke_test", "echo unsafe"),
        ]
        for section, key, value in examples:
            data = copy.deepcopy(MINIMAL)
            if key:
                data[section][key] = value
            else:
                data[section] = value
            with self.subTest(section=section, key=key, value=value), self.assertRaises(ContractError):
                ExperimentContract.from_dict(data)
        for bounds in ({"min": 5, "max": 2}, {"max": False}, {"equal": 2}, {}):
            data = {**MINIMAL, "constraints": {"latency": bounds}}
            with self.subTest(bounds=bounds), self.assertRaises(ContractError):
                ExperimentContract.from_dict(data)
        data = copy.deepcopy(MINIMAL)
        data["objective"] = {"metric": "loss", "direction": "minimize", "target": 0.1}
        data["budget"] = {"max_experiments": 0, "max_gpu_hours": 0, "max_ai_credits": 0}
        self.assertEqual(ExperimentContract.from_dict(data).rules.direction, "minimize")

    def test_paths_are_literal_relative_and_non_overlapping(self):
        for path in ("../model.py", "/tmp/file", "C:/model.py", "a\\b", "configs/*", "./model.py",
                     ".git/config", "CONFIGS/.GIT/config", "a//b", "x/../y", "foo. /x"):
            data = copy.deepcopy(MINIMAL)
            data["scope"]["editable"] = [path]
            with self.subTest(path=path), self.assertRaises(ContractError):
                ExperimentContract.from_dict(data)
        for path in ("evaluate.py", "DATA/TEST/", "data/", ".research_intern/", "job.yaml", "experiment_outputs/"):
            data = copy.deepcopy(MINIMAL)
            data["scope"]["editable"] = [path]
            with self.subTest(overlap=path), self.assertRaises(ContractError):
                ExperimentContract.from_dict(data)

    def test_custom_output_paths_and_collisions(self):
        paths = OutputPaths(root="results/export", run="meta/run.json", metrics="scores.json",
                            history="history.json", logs="diagnostics", artifacts="models")
        self.assertEqual(paths.root, "results/export/")
        for fields in ({"run": "metrics.json"}, {"run": "logs/file.json"}, {"logs": "artifacts"},
                       {"run": "meta", "history": "meta/history.json"}, {"metrics": "../scores.json"}):
            with self.subTest(fields=fields), self.assertRaises(ContractError):
                OutputPaths(**fields)


class ContractLoadingTests(WorkspaceTest):
    def test_yaml_rejects_duplicates_aliases_tags_and_multiple_documents(self):
        path = self.root / "input.yaml"
        for text in ("key: 1\nkey: 2\n", "a: &x [1]\nb: *x\n", "!!python/object:bad {}",
                     "one: 1\n---\ntwo: 2\n", "true: 1\n", "x: {a: 1, a: 2}\n"):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ContractError):
                read_yaml(path)

    def test_yaml_size_and_link_checks(self):
        path = self.root / "input.yaml"
        path.write_bytes(b"x" * (256 * 1024 + 1))
        with self.assertRaises(ContractError):
            read_yaml(path)
        path.unlink()
        target = self.root / "other.yaml"
        target.write_text("a: 1", encoding="utf-8")
        self.create_symlink(path, target)
        with self.assertRaises(ContractError):
            read_yaml(path)

    def test_loader_checks_job_and_protected_paths(self):
        repository, _ = prepare_candidate_fixture(self.root)
        self.assertEqual(load_contract(repository).rules.metric, "validation_f1")
        (repository / "azure_job.yaml").unlink()
        with self.assertRaises(ContractError):
            load_contract(repository)
        (repository / "azure_job.yaml").write_text("[]", encoding="utf-8")
        with self.assertRaises(ContractError):
            load_contract(repository)
        (repository / "azure_job.yaml").write_text("type: command", encoding="utf-8")
        (repository / "evaluate.py").unlink()
        with self.assertRaises(ContractError):
            load_contract(repository)


class MutationProposer:
    backend = "simulated"

    def __init__(self, mutate):
        self.mutate = mutate

    async def run_iteration(self, context, directory):
        self.mutate(context, directory)
        return CandidatePlan(context.selected_parent.experiment_id, "Observed baseline", "Tentative diagnosis",
                             "A change may help", "Change labeller only", "Measure externally")


class CandidateFixture(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.repository, self.baseline = prepare_candidate_fixture(self.root)
        self.git = GitWorkspace(self.root, self.repository)
        self.contract = load_contract(self.repository)
        self.ledger = Ledger(self.root, self.contract.rules, contract=self.contract, repository=self.repository)
        self.addCleanup(self.ledger.close)
        self.executor = SimulatedExecutor(self.root, self.contract.rules, paths=self.contract.outputs)
        self.execution = ExecutionController(self.ledger, self.executor)
        write_outputs(self.ledger.outputs("EXP-000"), self.contract.rules,
                      JobRequest("EXP-000", None, self.baseline), score=0.8)
        self.execution.import_baseline(self.baseline)

    def prepare(self, factory=ScriptedEditor, **kwargs):
        return asyncio.run(CandidateController(self.ledger, self.git, factory, **kwargs).prepare_candidate())

    def complete(self, record, scenario="improve"):
        self.executor.scenario = scenario
        record = self.execution.resume_submission(record.experiment_id)
        for _ in range(3):
            if record.terminal:
                return record
            record = self.execution.advance(record.experiment_id)
        self.fail("Simulated execution did not finish")

    def assert_unreserved(self):
        self.assertEqual(len(self.ledger.history()), 1)
        self.assertEqual(build_research_state(self.ledger.snapshot()).budget.remaining_experiments, 3)
        self.assertEqual(list(self.executor.jobs.iterdir()), [])
        self.assertFalse((self.root / "candidate.lock").exists())


class CandidateIntegrationTests(CandidateFixture):
    def test_allowed_edit_is_committed_evaluated_and_remembered(self):
        record = self.prepare()
        self.assertEqual(record.state, "PREPARED")
        self.assertEqual(record.parent_commit, self.baseline)
        self.assertEqual(record.git_commit, self.git.head)
        self.assertIn("labeller.py", record.diff)
        evidence = json.loads((self.ledger.directory(record.experiment_id) / "candidate.json").read_text())
        self.assertEqual(evidence["plan"]["parent_experiment"], "EXP-000")
        self.assertEqual(evidence["validation"]["changed_paths"], ["labeller.py"])
        self.assertEqual(self.complete(record).decision, "KEEP")
        state = build_research_state(self.ledger.snapshot())
        self.assertEqual(state.best_experiment.experiment_id, "EXP-001")
        self.assertEqual(state.budget.remaining_experiments, 2)

    def test_regression_restores_best_before_next_proposer_and_retains_commits(self):
        first = self.complete(self.prepare())
        best_code = (self.repository / "labeller.py").read_bytes()
        second = self.complete(self.prepare(lambda: ScriptedEditor(0.02)), "regress")
        self.assertEqual(second.decision, "REJECT")

        def inspect(context, directory):
            self.assertEqual(context.selected_parent.experiment_id, first.experiment_id)
            self.assertEqual(context.research_state.last_experiment.experiment_id, second.experiment_id)
            self.assertEqual((directory / "labeller.py").read_bytes(), best_code)
            self.assertEqual(context.contract, self.contract)
            self.assertEqual(self.git.head, first.git_commit)
            (directory / "labeller.py").write_text("WEIGHT_DECAY = 0.005\n", encoding="utf-8")

        third = self.prepare(lambda: MutationProposer(inspect))
        self.assertEqual(third.parent_commit, first.git_commit)
        refs = self.git._git("show-ref").decode()
        self.assertIn(second.git_commit, refs)
        self.assertEqual(third.experiment_id, "EXP-003")

    def test_protected_edit_is_blocked_even_when_plan_claims_an_allowed_change(self):
        with self.assertRaises(PermissionViolation):
            self.prepare(lambda: MutationProposer(lambda c, p: (p / "evaluate.py").write_text("tampered")))
        self.assert_unreserved()
        self.assertEqual(self.git.head, self.baseline)
        failures = list((self.root / "candidate_attempts").glob("*/failure.json"))
        self.assertEqual(json.loads(failures[0].read_text())["category"], "PERMISSION_VIOLATION")
        self.assertEqual((self.repository / "evaluate.py").read_text(), "tampered")

    def test_ignored_out_of_scope_file_is_detected(self):
        with self.assertRaises(PermissionViolation):
            self.prepare(lambda: MutationProposer(lambda c, p: (p / "hidden.cache").write_text("tampered")))
        self.assert_unreserved()

    def test_unversioned_empty_directory_is_rejected(self):
        with self.assertRaisesRegex(WorkspaceError, "Empty directories"):
            self.prepare(lambda: MutationProposer(lambda c, p: (p / "configs" / "empty").mkdir()))
        self.assert_unreserved()

    def test_protected_deletion_and_rename_are_blocked(self):
        with self.assertRaises(PermissionViolation):
            self.prepare(lambda: MutationProposer(lambda c, p: (p / "evaluate.py").rename(p / "configs" / "moved.py")))
        self.assert_unreserved()

    def test_allowed_new_and_deleted_files_have_exact_git_state(self):
        def mutate(context, path):
            (path / "configs" / "training.json").unlink()
            (path / "configs" / "new.py").write_text("AUGMENTATION = True\n", encoding="utf-8")
        record = self.prepare(lambda: MutationProposer(mutate))
        tree = self.git._tree(record.git_commit)
        self.assertIn("configs/new.py", tree)
        self.assertNotIn("configs/training.json", tree)

    def test_contract_change_is_blocked(self):
        with self.assertRaises(PermissionViolation):
            self.prepare(lambda: MutationProposer(lambda c, p: (p / ".research_intern" / "contract.yaml").write_text("budget: 999")))
        self.assert_unreserved()

    def test_git_metadata_mutation_is_blocked_without_executing_it(self):
        def mutate(context, path):
            config = path / ".git" / "config"
            config.write_text(config.read_text() + "\n[core]\nfsmonitor = dangerous-command\n")
        with self.assertRaises(PermissionViolation):
            self.prepare(lambda: MutationProposer(mutate))
        self.assert_unreserved()

    def test_unsafe_existing_git_configuration_is_rejected_before_proposal(self):
        config = self.repository / ".git" / "config"
        config.write_text(config.read_text() + "\n[core]\nfsmonitor = dangerous-command\n")
        factory = Mock()
        with self.assertRaisesRegex(WorkspaceError, "basic Git core"):
            self.prepare(factory)
        factory.assert_not_called()
        self.assert_unreserved()

    def test_proposer_cannot_stage_even_an_allowed_edit(self):
        def mutate(context, path):
            (path / "labeller.py").write_text("VALUE = 1\n")
            self.git._git("add", "labeller.py")
        with self.assertRaises(PermissionViolation):
            self.prepare(lambda: MutationProposer(mutate))
        self.assert_unreserved()

    def test_symlink_and_hardlink_changes_are_rejected(self):
        link = self.repository / "configs" / "linked.py"
        for make_link in (lambda: os.link(self.repository / "evaluate.py", link),
                  lambda: self.create_symlink(link, self.repository / "evaluate.py")):
            with self.subTest(link=make_link), self.assertRaises(WorkspaceError):
                self.prepare(lambda: MutationProposer(lambda c, p: make_link()))
            link.unlink(missing_ok=True)
        self.assert_unreserved()

    def test_preflight_catches_invalid_python_without_executing_code(self):
        with self.assertRaises(PreflightError):
            self.prepare(lambda: MutationProposer(lambda c, p: (p / "labeller.py").write_text("def broken(:")))
        self.assert_unreserved()
        (self.repository / "labeller.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
        self.assertEqual(len(validate_files(self.repository, ("labeller.py",))), 1)

    def test_preflight_catches_invalid_json_and_yaml_configuration(self):
        path = self.repository / "configs" / "training.json"
        for name, value in (("training.json", '{"a": 1, "a": 2}'), ("new.yaml", 'a: [broken')):
            target = self.repository / "configs" / name
            with self.subTest(name=name), self.assertRaises(PreflightError):
                self.prepare(lambda: MutationProposer(lambda c, p: target.write_text(value)))
            if target == path:
                target.write_text('{"batch_size": 4}\n')
            else:
                target.unlink()
        self.assert_unreserved()

    def test_preflight_rechecks_files_after_validation(self):
        def validate_then_mutate(repository, changed):
            result = validate_files(repository, changed)
            (repository / "evaluate.py").write_text("tampered after validation")
            return result
        with patch("research_intern.controller.candidate.validate_files", side_effect=validate_then_mutate):
            with self.assertRaises(PermissionViolation):
                self.prepare()
        self.assert_unreserved()

    def test_no_change_or_wrong_parent_plan_does_not_reserve(self):
        with self.assertRaises(WorkspaceError):
            self.prepare(lambda: MutationProposer(lambda c, p: None))
        class WrongParent(ScriptedEditor):
            async def run_iteration(self, context, directory):
                return replace(await super().run_iteration(context, directory), parent_experiment="EXP-099")
        with self.assertRaises(PreflightError):
            self.prepare(WrongParent)
        self.assert_unreserved()

    def test_stop_during_proposal_discards_candidate(self):
        def mutate(context, path):
            (path / "labeller.py").write_text("VALUE = 1\n")
            self.ledger.request_stop()
        with self.assertRaises(HandoffBlocked):
            self.prepare(lambda: MutationProposer(mutate))
        self.assert_unreserved()
        factory = Mock()
        with self.assertRaises(HandoffBlocked):
            self.prepare(factory)
        factory.assert_not_called()

    def test_proposer_timeout_and_failure_preserve_diagnostics(self):
        class Slow:
            backend = "simulated"
            async def run_iteration(self, context, directory):
                await asyncio.sleep(10)
        with self.assertRaisesRegex(SliceError, "timed out"):
            self.prepare(Slow, timeout_seconds=0.01)
        def fail(context, path):
            raise RuntimeError("fixture error")
        with self.assertRaisesRegex(RuntimeError, "fixture error"):
            self.prepare(lambda: MutationProposer(fail))
        self.assert_unreserved()
        self.assertEqual(len(list((self.root / "candidate_attempts").glob("*/failure.json"))), 2)

    def test_live_proposer_and_overlapping_preparation_are_blocked(self):
        live = Mock(backend="live")
        with self.assertRaises(HandoffBlocked):
            self.prepare(lambda: live)
        live.run_iteration.assert_not_called()
        (self.root / "candidate.lock").write_text("existing attempt")
        factory = Mock()
        with self.assertRaises(WorkspaceError):
            self.prepare(factory)
        factory.assert_not_called()
        self.assertEqual((self.root / "candidate.lock").read_text(), "existing attempt")

    def test_pending_experiment_blocks_new_edits(self):
        record = self.prepare()
        factory = Mock()
        with self.assertRaisesRegex(HandoffBlocked, "EXPERIMENT_IN_PROGRESS"):
            self.prepare(factory)
        factory.assert_not_called()
        self.assertEqual(self.git.head, record.git_commit)

    def test_cancellation_releases_lock_and_records_interruption(self):
        class Cancelled:
            backend = "simulated"
            async def run_iteration(self, context, directory):
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.prepare(Cancelled)
        self.assert_unreserved()
        failures = list((self.root / "candidate_attempts").glob("*/failure.json"))
        self.assertEqual(json.loads(failures[0].read_text())["category"], "INTERRUPTED")

    def test_dirty_and_unrecorded_commits_are_never_discarded(self):
        (self.repository / "labeller.py").write_text("HUMAN_WORK = 1\n")
        # Even an index hint must not hide an actual changed file.
        self.git._git("update-index", "--assume-unchanged", "labeller.py")
        factory = Mock()
        with self.assertRaises(WorkspaceError):
            self.prepare(factory)
        self.assertIn("HUMAN_WORK", (self.repository / "labeller.py").read_text())
        factory.assert_not_called()
        self.git._git("update-index", "--no-assume-unchanged", "labeller.py")
        self.git._git("add", "labeller.py")
        self.git._git("commit", "-m", "Unrecorded human work")
        with self.assertRaisesRegex(WorkspaceError, "Unrecorded"):
            self.prepare(factory)

    def test_platform_and_external_git_directories_are_rejected(self):
        with self.assertRaises(WorkspaceError):
            GitWorkspace(self.root, WORKSPACE)
        with self.assertRaises(WorkspaceError):
            GitWorkspace(WORKSPACE, self.repository)
        linked = self.root / "linked_repo"
        linked.mkdir()
        (linked / ".git").write_text(f"gitdir: {WORKSPACE / '.git'}\n")
        with self.assertRaises(WorkspaceError):
            GitWorkspace(self.root, linked)

    def test_contract_and_repository_binding_survive_reopen(self):
        with Ledger(self.root, self.contract.rules) as reopened:
            self.assertEqual(reopened.contract, self.contract)
            self.assertEqual(reopened.repository, self.repository)
        changed = self.contract.to_dict()
        changed["scope"]["protected"].append("extra.py")
        with self.assertRaises(LedgerError):
            Ledger(self.root, self.contract.rules, contract=ExperimentContract.from_dict(changed), repository=self.repository)
        with self.assertRaises(LedgerError):
            Ledger(self.root, self.contract.rules, contract=self.contract, repository=self.root)


class CustomOutputsTests(WorkspaceTest):
    def test_custom_outputs_flow_through_baseline_execution_and_reopen(self):
        repository, baseline = prepare_candidate_fixture(self.root)
        data = load_contract(repository).to_dict()
        data["outputs"] = {"root": "results/export/", "run": "meta/run.json", "metrics": "scores.json",
                           "history": "curve.json", "logs": "diagnostics/", "artifacts": "models/"}
        (repository / ".research_intern" / "contract.yaml").write_text(json.dumps(data))
        git = GitWorkspace(self.root, repository)
        git._git("add", "--all")
        git._git("commit", "-m", "Custom output contract fixture")
        baseline = git.head
        contract = load_contract(repository)
        with Ledger(self.root, contract.rules, contract=contract, repository=repository) as ledger:
            executor = SimulatedExecutor(self.root, contract.rules, paths=contract.outputs)
            execution = ExecutionController(ledger, executor)
            write_outputs(ledger.outputs("EXP-000"), contract.rules, JobRequest("EXP-000", None, baseline),
                          score=0.80, paths=contract.outputs)
            execution.import_baseline(baseline)
            record = asyncio.run(CandidateController(ledger, git, ScriptedEditor).prepare_candidate())
            record = execution.resume_submission(record.experiment_id)
            for _ in range(2):
                record = execution.advance(record.experiment_id)
            self.assertEqual(record.decision, "KEEP")
            self.assertTrue((ledger.outputs(record.experiment_id) / "scores.json").is_file())
        with Ledger(self.root, contract.rules) as reopened:
            self.assertEqual(reopened.output_paths, contract.outputs)
            self.assertEqual(reopened.get("EXP-001").decision, "KEEP")

    def test_candidate_cli_is_offline_and_reports_retained_evidence(self):
        with patch("research_intern.candidate_demo.run_candidate_demo") as run:
            run.return_value = (self.root, Mock(budget=Mock(remaining_experiments=3)))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["candidate-demo", "--scenario", "protected"]), 0)
            self.assertIn("Copilot usage: 0", output.getvalue())
            run.assert_awaited_once()
