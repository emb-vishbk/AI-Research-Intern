"""Offline research memory, stopping, and fresh-proposal integration checks."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sqlite3
from dataclasses import asdict, replace
from unittest.mock import AsyncMock, Mock, patch

from research_intern.controller.execution import ExecutionController
from research_intern.controller.handoff import HandoffBlocked, HandoffController, build_iteration_context
from research_intern.copilot.simulated import ScriptedProposer
from research_intern.domain.experiments import JobRequest
from research_intern.domain.research import CandidatePlan
from research_intern.execution.simulated import SimulatedExecutor, write_outputs
from research_intern.handoff_demo import run_handoff_demo
from research_intern.ledger.research_state import build_research_state
from research_intern.ledger.sqlite import Ledger, LedgerError
from research_intern.main import main

from test_execution_slice import BASE_COMMIT, RULES, WorkspaceTest, candidate


class HandoffFixture(WorkspaceTest):
    def prepare_run(self, *, limit=4, baseline_score=0.8, rules=RULES):
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.ledger = Ledger(self.run_root, rules, max_experiments=limit)
        self.addCleanup(lambda: self.ledger.close())
        self.executor = SimulatedExecutor(self.run_root, rules)
        self.execution = ExecutionController(self.ledger, self.executor)
        self.fixture = self.run_root / "fixture"
        self.fixture.mkdir()
        write_outputs(self.ledger.outputs("EXP-000"), rules,
                      JobRequest("EXP-000", None, BASE_COMMIT), score=baseline_score)
        self.execution.import_baseline(BASE_COMMIT)

    def complete(self, scenario="improve", *, parent=None):
        self.executor.scenario = scenario
        parent = parent or self.ledger.best().experiment_id
        commit = f"{len(self.ledger.history()):040x}"
        record = self.execution.start(candidate(parent=parent, commit=commit))
        for _ in range(3):
            if record.terminal:
                return record
            record = self.execution.advance(record.experiment_id)
        self.fail("The simulated job did not reach a terminal record")

    def state(self, **options):
        return build_research_state(self.ledger.snapshot(), **options)

    def propose(self, factory=ScriptedProposer):
        return asyncio.run(HandoffController(self.ledger, factory).propose_next(self.fixture))


class ResearchStateTests(HandoffFixture):
    def test_baseline_state_and_unknown_service_budgets(self):
        self.prepare_run(limit=3)
        state = self.state()
        self.assertEqual(state.baseline.experiment_id, "EXP-000")
        self.assertEqual(state.best_experiment, state.last_experiment)
        self.assertEqual(state.budget.allocated_experiments, 0)
        self.assertEqual(state.budget.remaining_experiments, 3)
        self.assertIsNone(state.budget.remaining_gpu_hours)
        self.assertIsNone(state.budget.remaining_copilot_credits)
        self.assertEqual(state.supported_directions, ())
        self.assertTrue(state.continuation_allowed)

    def test_rejection_preserves_best_parent_and_exposes_constraint_evidence(self):
        self.prepare_run()
        self.complete()
        self.complete("constraint")
        state = self.state()
        context = build_iteration_context(state)
        self.assertEqual(state.last_experiment.experiment_id, "EXP-002")
        self.assertEqual(context.selected_parent.experiment_id, "EXP-001")
        self.assertEqual(state.rejected_directions[0].experiment_id, "EXP-002")
        check = state.last_experiment.constraint_results[0]
        self.assertEqual((check.actual, check.threshold, check.satisfied), (60, 50, False))
        self.assertEqual(state.budget.remaining_experiments, 2)
        self.assertIn("EXP-002", state.open_questions[-1])

    def test_failure_retains_negative_evidence_without_inventing_a_score(self):
        self.prepare_run()
        self.complete()
        self.complete("runtime-failure")
        state = self.state()
        self.assertEqual(state.best_experiment.experiment_id, "EXP-001")
        self.assertIsNone(state.last_experiment.score)
        self.assertEqual(state.last_experiment.failure_type, "EXECUTION_FAILED")
        self.assertEqual(state.known_failures[0].experiment_id, "EXP-002")
        self.assertEqual(state.rejected_directions, ())
        self.assertIn("EXP-002", state.open_questions[-1])

    def test_restart_rebuilds_identical_state_without_reading_an_export(self):
        self.prepare_run()
        self.complete()
        self.complete("regress")
        before = self.state()
        (self.run_root / "research_state.json").write_text('{"best": "invented"}', encoding="utf-8")
        self.ledger.close()
        self.ledger = Ledger(self.run_root, RULES)
        self.assertEqual(self.state(), before)
        self.assertEqual(json.dumps(asdict(self.state()), sort_keys=True),
                         json.dumps(asdict(before), sort_keys=True))

    def test_recent_window_preserves_older_negative_categories_and_bounds_text(self):
        self.prepare_run(limit=8)
        self.complete("regress")
        self.complete("runtime-failure")
        kept = self.complete()
        snapshot = self.ledger.snapshot()
        long_record = replace(kept, sequence=4, experiment_id="EXP-004", hypothesis="x" * 5000,
                              planned_intervention="y" * 5000, diff="SECRET RAW DIFF",
                              evaluation=replace(kept.evaluation, conclusion="z" * 5000))
        state = build_research_state(replace(snapshot, experiments=(*snapshot.experiments, long_record)),
                                     recent_limit=1)
        self.assertEqual([item.experiment_id for item in state.recent_results], ["EXP-004"])
        self.assertEqual(state.rejected_directions[0].experiment_id, "EXP-001")
        self.assertEqual(state.known_failures[0].experiment_id, "EXP-002")
        self.assertLessEqual(len(state.last_experiment.hypothesis), 600)
        self.assertLessEqual(len(state.last_experiment.planned_intervention), 600)
        self.assertNotIn("SECRET RAW DIFF", json.dumps(asdict(state)))

    def test_best_selection_matches_ledger_for_minimization(self):
        self.prepare_run(rules=replace(RULES, direction="minimize", target=0.1), baseline_score=0.95)
        self.complete("improve")
        self.assertEqual(self.state().best_experiment.experiment_id, self.ledger.best().experiment_id)
        self.assertEqual(self.state().best_experiment.score, 0.84)

    def test_pending_experiment_is_latest_and_blocks_a_new_proposal(self):
        self.prepare_run()
        record = self.execution.start(candidate())
        state = self.state()
        self.assertEqual(state.last_experiment.experiment_id, record.experiment_id)
        self.assertEqual(state.best_experiment.experiment_id, "EXP-000")
        self.assertEqual(state.recent_results, ())
        self.assertIn("EXPERIMENT_IN_PROGRESS", state.blocking_reasons)
        with self.assertRaises(HandoffBlocked):
            build_iteration_context(state)


class BudgetTests(HandoffFixture):
    def test_zero_budget_and_missing_baseline_do_not_create_a_proposer(self):
        self.prepare_run(limit=0)
        factory = Mock()
        with self.assertRaisesRegex(HandoffBlocked, "EXPERIMENT_BUDGET_EXHAUSTED"):
            self.propose(factory)
        with self.assertRaises(LedgerError):
            self.execution.start(candidate())
        factory.assert_not_called()
        self.assertEqual(len(self.ledger.history()), 1)
        empty = self.root / "empty"
        empty.mkdir()
        with Ledger(empty, RULES, max_experiments=2) as ledger:
            with self.assertRaisesRegex(HandoffBlocked, "BASELINE_MISSING"):
                asyncio.run(HandoffController(ledger, factory).propose_next(empty))
        factory.assert_not_called()

    def test_final_slot_can_resume_but_cannot_be_reallocated(self):
        self.prepare_run(limit=1)
        self.ledger.reserve(candidate())
        self.ledger.close()
        self.ledger = Ledger(self.run_root, RULES)
        self.execution = ExecutionController(self.ledger, self.executor)
        self.assertEqual(self.state().budget.remaining_experiments, 0)
        self.execution.resume_submission("EXP-001")
        self.execution.advance("EXP-001")
        self.execution.advance("EXP-001")
        with self.assertRaisesRegex(LedgerError, "EXPERIMENT_BUDGET_EXHAUSTED"):
            self.execution.start(candidate())
        factory = Mock()
        with self.assertRaises(HandoffBlocked):
            self.propose(factory)
        factory.assert_not_called()

    def test_failed_submission_consumes_one_slot_and_keeps_its_id(self):
        self.prepare_run(limit=2)
        record = self.complete("submission-failure")
        self.assertIsNone(record.job_id)
        self.assertEqual(self.state().budget.remaining_experiments, 1)
        second = self.complete("runtime-failure")
        self.assertEqual(second.experiment_id, "EXP-002")
        self.assertEqual(self.state().budget.remaining_experiments, 0)
        with self.assertRaises(LedgerError):
            self.execution.start(candidate())

    def test_invalid_candidate_and_draft_plan_do_not_spend_experiment_slots(self):
        self.prepare_run(limit=1)
        with self.assertRaises(LedgerError):
            self.execution.start(replace(candidate(), git_commit="HEAD"))
        self.propose()
        self.assertEqual(self.state().budget.allocated_experiments, 0)

    def test_goal_blocks_even_when_budget_remains_including_baseline_goal(self):
        self.prepare_run(limit=4)
        self.complete("goal")
        self.assertEqual(self.state().budget.remaining_experiments, 3)
        with self.assertRaisesRegex(LedgerError, "GOAL_REACHED"):
            self.execution.start(candidate())
        factory = Mock()
        with self.assertRaisesRegex(HandoffBlocked, "GOAL_REACHED"):
            self.propose(factory)
        factory.assert_not_called()
        snapshot = self.ledger.snapshot()
        baseline_goal = replace(snapshot.experiments[0], decision="GOAL_REACHED")
        state = build_research_state(replace(snapshot, experiments=(baseline_goal,)))
        self.assertIn("GOAL_REACHED", state.blocking_reasons)

    def test_stop_is_persistent_and_does_not_discard_running_results(self):
        self.prepare_run()
        record = self.execution.start(candidate())
        self.ledger.request_stop()
        self.ledger.request_stop()
        self.ledger.close()
        self.ledger = Ledger(self.run_root, RULES)
        self.execution = ExecutionController(self.ledger, self.executor)
        self.execution.advance(record.experiment_id)
        final = self.execution.advance(record.experiment_id)
        self.assertEqual(final.decision, "KEEP")
        self.assertIn("HUMAN_STOP", self.state().blocking_reasons)
        factory = Mock()
        with self.assertRaises(HandoffBlocked):
            self.propose(factory)
        factory.assert_not_called()

    def test_stop_before_submission_prevents_executor_call(self):
        self.prepare_run()
        self.ledger.reserve(candidate())
        self.ledger.request_stop()
        with patch.object(self.executor, "submit_job") as submit:
            with self.assertRaisesRegex(LedgerError, "HUMAN_STOP"):
                self.execution.resume_submission("EXP-001")
        submit.assert_not_called()
        self.assertEqual(self.ledger.get("EXP-001").state, "PREPARED")

    def test_budget_is_validated_immutable_and_visible_to_other_connections(self):
        self.prepare_run(limit=1)
        for value in (-1, True, 1.5, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Ledger(self.run_root, RULES, max_experiments=value)
        with self.assertRaisesRegex(LedgerError, "cannot change"):
            Ledger(self.run_root, RULES, max_experiments=2)
        with Ledger(self.run_root, RULES) as other:
            self.ledger.reserve(candidate())
            with self.assertRaises(LedgerError):
                other.reserve(candidate())
            self.assertEqual(other.snapshot().max_experiments, 1)
            self.assertEqual(len(other.history()), 2)

    def test_legacy_history_remains_readable_but_has_no_implicit_budget(self):
        self.prepare_run(limit=None)
        state = self.state()
        self.assertEqual(state.baseline.experiment_id, "EXP-000")
        self.assertIsNone(state.budget.remaining_experiments)
        self.assertIn("BUDGET_NOT_CONFIGURED", state.blocking_reasons)
        with self.assertRaises(LedgerError):
            self.execution.start(candidate())
        with self.assertRaisesRegex(LedgerError, "before importing the baseline"):
            Ledger(self.run_root, RULES, max_experiments=8)


class ProposalTests(HandoffFixture):
    def test_fresh_instances_receive_new_evidence_and_selected_parent(self):
        self.prepare_run()
        self.complete()
        instances = []

        def factory():
            proposer = ScriptedProposer()
            proposer.run_iteration = AsyncMock(wraps=proposer.run_iteration)
            instances.append(proposer)
            return proposer

        first = self.propose(factory)
        self.complete("regress")
        second = self.propose(factory)
        self.assertEqual(len(instances), 2)
        self.assertIsNot(instances[0], instances[1])
        for instance in instances:
            instance.run_iteration.assert_awaited_once()
        self.assertEqual(first.context.research_state.last_experiment.experiment_id, "EXP-001")
        self.assertEqual(second.context.research_state.last_experiment.experiment_id, "EXP-002")
        self.assertEqual(second.plan.parent_experiment, "EXP-001")
        self.assertEqual(second.context.selected_parent.git_commit, self.ledger.get("EXP-001").git_commit)
        self.assertIn("EXP-002", second.plan.observation)
        self.assertNotEqual(first.plan.planned_intervention, second.plan.planned_intervention)
        self.assertEqual(len(self.ledger.history()), 3)  # Proposals do not allocate EXP-003.
        self.assertEqual(list(self.fixture.iterdir()), [])

    def test_new_history_during_proposal_invalidates_returned_plan(self):
        self.prepare_run()

        async def mutate(context, working_directory):
            self.complete()
            return await ScriptedProposer().run_iteration(context, working_directory)

        proposer = ScriptedProposer()
        proposer.run_iteration = AsyncMock(side_effect=mutate)
        with self.assertRaisesRegex(HandoffBlocked, "stale proposal"):
            self.propose(lambda: proposer)

    def test_human_stop_during_proposal_invalidates_returned_plan(self):
        self.prepare_run()

        async def stop(context, working_directory):
            self.ledger.request_stop()
            return await ScriptedProposer().run_iteration(context, working_directory)

        proposer = ScriptedProposer()
        proposer.run_iteration = AsyncMock(side_effect=stop)
        with self.assertRaisesRegex(HandoffBlocked, "stale proposal"):
            self.propose(lambda: proposer)

    def test_wrong_parent_unstructured_output_and_live_proposer_are_rejected(self):
        self.prepare_run()
        valid = self.propose().plan
        for result in (replace(valid, parent_experiment="EXP-999"), {"decision": "KEEP"}):
            proposer = ScriptedProposer()
            proposer.run_iteration = AsyncMock(return_value=result)
            with self.subTest(result=result), self.assertRaises(HandoffBlocked):
                self.propose(lambda: proposer)
        proposer = ScriptedProposer()
        proposer.backend = "copilot"
        proposer.run_iteration = AsyncMock()
        with self.assertRaisesRegex(HandoffBlocked, "live proposers"):
            self.propose(lambda: proposer)
        proposer.run_iteration.assert_not_awaited()

    def test_proposer_failure_does_not_create_a_scientific_experiment(self):
        self.prepare_run()
        before = self.ledger.snapshot()
        proposer = ScriptedProposer()
        proposer.run_iteration = AsyncMock(side_effect=RuntimeError("Simulated proposer failure"))
        with self.assertRaises(RuntimeError):
            self.propose(lambda: proposer)
        self.assertEqual(self.ledger.snapshot(), before)

    def test_working_directory_must_remain_inside_the_simulation(self):
        self.prepare_run()
        factory = Mock()
        with self.assertRaises(HandoffBlocked):
            asyncio.run(HandoffController(self.ledger, factory).propose_next(self.root))
        factory.assert_not_called()

    def test_plan_fields_are_concise_and_nonempty(self):
        for value in ("", " " * 2, "x" * 2001, None):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                CandidatePlan("EXP-000", "observation", "diagnosis", value, "change", "effect")


class HandoffDemoTests(WorkspaceTest):
    def test_demo_connects_git_results_state_and_fresh_proposals(self):
        with contextlib.redirect_stdout(io.StringIO()):
            root, state = asyncio.run(run_handoff_demo(self.root))
        first = json.loads((root / "handoff-after-EXP-001.json").read_text(encoding="utf-8"))
        second = json.loads((root / "handoff-after-EXP-002.json").read_text(encoding="utf-8"))
        self.assertEqual(first["context"]["selected_parent"]["experiment_id"], "EXP-001")
        self.assertEqual(second["context"]["selected_parent"]["experiment_id"], "EXP-001")
        self.assertEqual(second["context"]["research_state"]["last_experiment"]["decision"], "REJECT")
        self.assertEqual(state.budget.remaining_experiments, 1)
        with sqlite3.connect(root / "ledger.sqlite3") as database:
            row = database.execute("SELECT planned_intervention, parent_commit FROM experiments WHERE experiment_id='EXP-002'").fetchone()
            best_commit = database.execute("SELECT git_commit FROM experiments WHERE experiment_id='EXP-001'").fetchone()[0]
        self.assertEqual(row[0], first["plan"]["planned_intervention"])
        self.assertEqual(row[1], best_commit)
        self.assertEqual(second["context"]["selected_parent"]["git_commit"], best_commit)
        self.assertTrue((root / "fixture_repo" / ".git").is_dir())

    def test_zero_budget_demo_stops_before_candidate_or_proposer(self):
        with patch("research_intern.handoff_demo.ScriptedProposer") as proposer:
            with contextlib.redirect_stdout(io.StringIO()):
                root, state = asyncio.run(run_handoff_demo(self.root, max_experiments=0))
        proposer.assert_not_called()
        self.assertEqual(state.budget.allocated_experiments, 0)
        self.assertIn("EXPERIMENT_BUDGET_EXHAUSTED", state.blocking_reasons)
        self.assertEqual(list(root.glob("handoff-after-*.json")), [])

    def test_cli_validates_budget_without_starting_a_demo(self):
        with patch("research_intern.handoff_demo.run_handoff_demo") as run:
            for value in ("-1", "1.5", "nan"):
                with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(["handoff-demo", "--max-experiments", value])
                self.assertEqual(raised.exception.code, 2)
        run.assert_not_called()
