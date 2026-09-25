"""Shared credit accounting, human additions and cancellation; fake providers only."""
import asyncio
import copy
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from research_intern.copilot.live import CopilotPaused
from research_intern.ledger.credits import credit_guidance, reconcile_legacy_rejections
from research_intern.ledger.services import ServiceJournal, ServiceAdmissionError
from research_intern.ledger.sqlite import Ledger
from research_intern.ledger.preparations import PreparationJournal
from research_intern.controller.mission import MissionControl
from research_intern.domain.experiments import SliceError
from test_execution_slice import WorkspaceTest
from test_service_adapters import LiveCopilotTests
from test_live_loop import LiveLoopTests


class PoolJournalTests(WorkspaceTest):
    def pool(self):
        return ServiceJournal(self.root, service="copilot_credits", unit="microcredits", max_units=100_000_000)

    def test_settlement_returns_unused_pool_and_survives_restart(self):
        pool = self.pool()
        pool.reserve("attempt-1", {}, units=100_000_000)
        self.assertEqual(pool.usage()["remaining"], 0)
        proof = {"kind": "provider_usage", "total_nano_aiu": 12_500_000_000}
        pool.settle("attempt-1", 12_500_000, proof)
        pool.settle("attempt-1", 12_500_000, proof)
        self.assertEqual(self.pool().usage()["remaining"], 87_500_000)
        self.assertEqual(pool.get("attempt-1").units, 100_000_000)
        with self.assertRaises(ServiceAdmissionError): pool.settle("attempt-1", 0, proof)
        with self.assertRaises(ServiceAdmissionError): pool.reserve("attempt-2", {}, units=100_000_000)

    def test_unknown_usage_kept_and_human_addition_is_idempotent(self):
        pool = self.pool()
        pool.reserve("attempt-1", {}, units=100_000_000)
        pool.add_credits("addition-1", 50_000_000, "a" * 64)
        pool.add_credits("addition-1", 50_000_000, "a" * 64)
        usage = self.pool().usage()
        self.assertEqual((usage["limit"], usage["held"], usage["remaining"]), (150_000_000, 100_000_000, 50_000_000))
        self.assertIsNone(usage["actual_usage"])
        with self.assertRaises(ServiceAdmissionError): pool.add_credits("addition-1", 60_000_000, "a" * 64)

    def test_estimate_uses_observed_usage_and_is_unknown_without_it(self):
        pool = self.pool()
        self.assertIsNone(credit_guidance(pool, 3)["estimated_additional"])
        pool.reserve("attempt-1", {}, units=100_000_000)
        pool.settle("attempt-1", 85_000_000, {"kind": "provider_usage"})
        guidance = credit_guidance(pool, 2)
        self.assertEqual(guidance["minimum_additional"], 15)
        self.assertEqual(guidance["sample_count"], 1)
        self.assertEqual(guidance["estimated_additional"], 198)

    def test_soft_limit_overrun_is_counted_without_clamping(self):
        pool = self.pool()
        pool.reserve("attempt-1", {}, units=100_000_000)
        pool.settle("attempt-1", 102_000_000, {"kind": "provider_usage"})
        self.assertEqual(pool.usage()["remaining"], -2_000_000)
        self.assertEqual(credit_guidance(pool, 1)["minimum_additional"], 32)


class SharedCopilotTests(LiveCopilotTests):
    def pool(self, nano=12_500_000_000):
        self.session.rpc = SimpleNamespace(usage=SimpleNamespace(get_metrics=AsyncMock(
            return_value=SimpleNamespace(total_nano_aiu=nano))))
        return ServiceJournal(self.root, service="copilot_credits", unit="microcredits", max_units=100_000_000)

    async def test_each_session_uses_remaining_pool_not_experiment_fraction(self):
        pool = self.pool()
        await self.adapter(credit_journal=pool).run_iteration(self.context, self.repository)
        self.assertEqual(self.client.create_session.call_args.kwargs["session_limits"], {"max_ai_credits": 100})
        self.assertEqual(pool.usage()["remaining"], 87_500_000)
        await self.adapter(credit_journal=pool).run_iteration(replace(self.context, attempt_number=2), self.repository)
        self.assertEqual(self.client.create_session.call_args.kwargs["session_limits"], {"max_ai_credits": 87.5})
        self.assertEqual(pool.usage()["remaining"], 75_000_000)

    async def test_insufficient_pool_never_starts_client(self):
        pool = self.pool()
        pool.reserve("prior", {}, units=71_000_000)
        with self.assertRaises(CopilotPaused):
            await self.adapter(credit_journal=pool).run_iteration(self.context, self.repository)
        self.constructor.assert_not_called()
        self.assertEqual(self.journal.usage()["reserved"], 0)

    async def test_rejected_session_releases_pool_and_turn_without_sending(self):
        pool = self.pool()
        self.client.create_session.side_effect = RuntimeError("minimum session limit")
        with self.assertRaises(CopilotPaused):
            await self.adapter(credit_journal=pool).run_iteration(self.context, self.repository)
        self.session.send_and_wait.assert_not_awaited()
        self.assertEqual(pool.usage()["remaining"], 100_000_000)
        self.assertEqual(self.journal.usage()["reserved"], 0)

    async def test_missing_usage_retains_reservation(self):
        pool = self.pool(nano=None)
        await self.adapter(credit_journal=pool).run_iteration(self.context, self.repository)
        self.assertEqual(pool.usage()["held"], 100_000_000)

    async def test_failed_abort_does_not_release_possibly_incomplete_usage(self):
        pool = self.pool(nano=1)
        self.session.send_and_wait.side_effect = RuntimeError("lost response")
        self.session.abort.side_effect = RuntimeError("abort not confirmed")
        with self.assertRaises(CopilotPaused):
            await self.adapter(credit_journal=pool).run_iteration(self.context, self.repository)
        self.session.rpc.usage.get_metrics.assert_not_awaited()
        self.assertEqual(pool.usage()["held"], 100_000_000)

    async def test_shutdown_failure_is_surfaced_for_recovery(self):
        from research_intern.copilot.live import CopilotShutdownError
        pool = self.pool()
        self.client.stop.side_effect = RuntimeError("shutdown failed")
        with self.assertRaises(CopilotShutdownError):
            await self.adapter(credit_journal=pool).run_iteration(self.context, self.repository)
        self.client.force_stop.assert_awaited_once()

    async def test_lost_signin_pauses_instead_of_retrying_zero_cost_failures(self):
        pool = self.pool()
        self.client.get_auth_status.return_value = SimpleNamespace(isAuthenticated=False)
        with self.assertRaises(CopilotPaused):
            await self.adapter(credit_journal=pool).run_iteration(self.context, self.repository)
        self.assertEqual(pool.usage()["remaining"], 100_000_000)
        self.session.send_and_wait.assert_not_awaited()

    async def test_exhaustion_event_aborts_and_pauses_instead_of_waiting_for_timeout(self):
        pool = self.pool(nano=101_000_000_000)
        async def exhausted(*args, **kwargs):
            handler = self.session.on.call_args.args[0]
            handler(SimpleNamespace(type="session_limits_exhausted.requested"))
            await asyncio.Event().wait()
        self.session.send_and_wait.side_effect = exhausted
        with self.assertRaises(CopilotPaused) as raised:
            await asyncio.wait_for(self.adapter(credit_journal=pool).run_iteration(self.context, self.repository), 2)
        self.assertEqual(raised.exception.pause_reason, "AI_CREDIT_BUDGET_EXHAUSTED")
        self.session.abort.assert_awaited_once()
        self.assertEqual(pool.usage()["spent"], 101_000_000)


class PoolLiveTests(LiveLoopTests):
    def test_legacy_repair_retains_attempts_and_exempts_only_proven_rejections(self):
        self.run_live()
        _, turns, credits = self.live.journals(self.live.configuration())
        with Ledger.reopen(self.live.root) as ledger:
            journal = PreparationJournal(ledger)
            baseline = ledger.best()
            attempt = journal.begin(baseline.experiment_id, baseline.git_commit)
            journal.update(attempt.attempt_id, "FAILED", failure_type="PROPOSER_FAILED", failure_message="JsonRpcError")
            journal.update(attempt.attempt_id, "RECOVERED")
            turns.reserve("attempt-1", {}, units=1)
            credits.reserve("attempt-1", {"limit": 16}, units=16_000_000)
            log = b"CopilotClient.create_session failed\nMinimum session limit is 30 AI credits."
            with self.assertRaises(ServiceAdmissionError):
                reconcile_legacy_rejections(turns, credits, ["attempt-1"], b"unknown timeout")
            self.assertEqual(reconcile_legacy_rejections(turns, credits, ["attempt-1"], log), 16_000_000)
            self.assertEqual(reconcile_legacy_rejections(turns, credits, ["attempt-1"], log), 0)
            self.assertEqual(journal.count(), 0)
            self.assertEqual(journal.latest().sequence, 1)
            self.assertEqual(journal.begin(baseline.experiment_id, baseline.git_commit).sequence, 2)
            self.assertEqual(credits.usage()["remaining"], 100_000_000)
            self.assertEqual(turns.usage()["reserved"], 0)

    def test_addition_preserves_contract_baseline_and_service_verification(self):
        self.run_live()
        config_before = self.live.configuration()
        _, _, pool = self.live.journals(config_before)
        pool.reserve("prior", {}, units=100_000_000)
        mission = MissionControl(self.root)
        with patch.object(mission, "start") as start:
            mission.add_credits(50, "add-1", config_before["contract_sha256"])
            start.assert_not_called()
        self.assertEqual(pool.usage()["limit"], 150_000_000)
        self.assertEqual(config_before, self.live.configuration())
        self.assertTrue(self.live.inspect()["baseline_accepted"])
        self.assertNotIn("AI_CREDIT_BUDGET_EXHAUSTED", self.live.inspect()["continuation_blockers"])
        with self.assertRaises(SliceError): self.live.add_credits(50, "add-2", "f" * 64)
        with Ledger.reopen(self.live.root) as ledger: ledger.request_stop()
        with self.assertRaises(SliceError): self.live.add_credits(50, "add-2", config_before["contract_sha256"])

    def test_credit_api_validates_and_deduplicates(self):
        from fastapi.testclient import TestClient
        from research_intern.api.app import create_app
        body = {"additional_credits": 50, "request_id": "add-1", "contract_sha256": self.live.configuration()["contract_sha256"]}
        with TestClient(create_app(self.root), base_url="http://localhost") as client:
            for _ in range(2):
                response = client.post("/api/project/credits", headers={"x-research-intern": "1"}, json=body)
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["live"]["usage"]["credits"]["limit"], 150_000_000)
            for invalid in (0, -1, True, "50"):
                response = client.post("/api/project/credits", headers={"x-research-intern": "1"}, json={**body, "additional_credits": invalid})
                self.assertEqual(response.status_code, 422)

    def test_provider_configuration_error_pauses_after_one_attempt(self):
        self.run_live()
        self.live.activate()
        class Rejected:
            backend = "copilot"
            async def run_iteration(self, *args): raise CopilotPaused("Fix configuration")
        asyncio.run(self.live.run(gateway=self.gateway, proposer_factory=Rejected, emit=lambda _: None))
        with Ledger.reopen(self.live.root) as ledger:
            self.assertEqual(PreparationJournal(ledger).count(), 1)
            self.assertEqual(PreparationJournal(ledger).loop_status()["state"], "PAUSED")
        self.assertEqual(len(self.gateway.submissions), 1)

    def test_stop_cancels_owned_remote_job_and_does_not_start_candidate(self):
        self.run_live(steps=1)
        name = self.gateway.submissions[0][0]
        self.gateway.jobs[name] = replace(self.gateway.jobs[name], status="Running")
        original = self.gateway.cancel
        def cancel(job):
            original(job)
            self.gateway.jobs[job] = replace(self.gateway.jobs[job], status="Canceled")
        self.gateway.cancel = cancel
        with Ledger.reopen(self.live.root) as ledger: ledger.request_stop()
        result = self.run_live()
        self.assertEqual(self.gateway.cancelled, [name])
        self.assertEqual(result["controller"]["state"], "STOPPED")
        self.assertEqual(len(self.gateway.submissions), 1)
        self.assertFalse(self.calls)

    def test_stop_cancels_active_copilot_task(self):
        self.run_live()
        self.live.activate()
        entered, cancelled = threading.Event(), threading.Event()
        class Waiting:
            backend = "copilot"
            async def run_iteration(self, *args):
                entered.set()
                try: await asyncio.Event().wait()
                finally: cancelled.set()
        mission = MissionControl(self.root)
        original = mission.live.run
        async def run(**kwargs):
            return await original(gateway=self.gateway, proposer_factory=Waiting, **kwargs)
        with patch.object(mission.live, "run", side_effect=run):
            try:
                mission.start("live-project")
                self.assertTrue(entered.wait(10))
                mission.stop("live-project")
                self.assertTrue(cancelled.wait(5))
                mission._thread.join(10)
                self.assertFalse(mission.activity()["active_run_id"])
                self.assertEqual(mission.status("live-project")["controller"]["state"], "STOPPED")
                self.assertEqual(len(self.gateway.submissions), 1)
            finally: mission.close()
