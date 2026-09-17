"""Offline checks for the read boundary and SDK lifecycle; no model calls."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import create_autospec, patch
from uuid import uuid4

from copilot import CopilotClient, CopilotSession, GetAuthStatusResponse, ToolInvocation
from copilot.rpc import PermissionDecisionReject
from copilot.session_events import (
    AssistantMessageData,
    PermissionRequestCustomTool,
    PermissionRequestRead,
    SessionEvent,
    SessionEventType,
)

from research_intern.copilot import spike


def response_event(content: str = "A fixture experiment; marker TEST-README.") -> SessionEvent:
    return SessionEvent(
        data=AssistantMessageData(content=content, message_id="test-message"),
        id=uuid4(), timestamp=datetime.now(UTC), type=SessionEventType.ASSISTANT_MESSAGE,
    )


class WorkspaceFixture:
    def prepare_workspace(self) -> None:
        # Test fixtures and their cleanup stay inside the application workspace.
        parent = Path(__file__).resolve().parents[1] / ".runtime" / "test-workspaces"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "application"
        self.directory = self.workspace / "experiment"
        self.directory.mkdir(parents=True)
        self.readme = self.directory / "README.md"
        self.text = "# Fixture\nAn ordinary ML experiment. Marker: TEST-README.\n"
        self.readme.write_bytes(self.text.encode("utf-8"))
        self.runtime = self.workspace / "fake-runtime.exe"
        self.runtime.write_bytes(b"Not executable: all SDK calls are mocked.")


class ReadBoundaryTests(WorkspaceFixture, unittest.TestCase):
    def setUp(self) -> None:
        self.prepare_workspace()

    def test_reads_only_the_selected_readme(self) -> None:
        (self.directory / "private.txt").write_text("unrelated", encoding="utf-8")
        self.assertEqual(spike.read_readme(self.directory), self.text)

    def test_rejects_missing_directory_in_place_of_file_and_empty_readme(self) -> None:
        self.readme.unlink()
        with self.assertRaises(spike.SpikeError):
            spike.read_readme(self.directory)
        self.readme.mkdir()
        with self.assertRaises(spike.SpikeError):
            spike.read_readme(self.directory)
        self.readme.rmdir()
        self.readme.write_text(" \n\t", encoding="utf-8")
        with self.assertRaisesRegex(spike.SpikeError, "empty"):
            spike.read_readme(self.directory)

    def test_size_limit_is_in_bytes_and_accepts_exact_boundary(self) -> None:
        self.readme.write_bytes(b"a" * spike.MAX_README_BYTES)
        self.assertEqual(len(spike.read_readme(self.directory)), spike.MAX_README_BYTES)
        self.readme.write_bytes(b"a" * (spike.MAX_README_BYTES + 1))
        with self.assertRaisesRegex(spike.SpikeError, "32 KiB"):
            spike.read_readme(self.directory)

    def test_rejects_invalid_utf8(self) -> None:
        self.readme.write_bytes(b"\xff")
        with self.assertRaisesRegex(spike.SpikeError, "UTF-8"):
            spike.read_readme(self.directory)

    def test_rejects_read_errors(self) -> None:
        with patch.object(Path, "open", side_effect=PermissionError("unreadable")):
            with self.assertRaisesRegex(spike.SpikeError, "readable UTF-8"):
                spike.read_readme(self.directory)

    def test_rejects_readme_symlink(self) -> None:
        target = self.root / "outside-readme.md"
        target.write_text("outside", encoding="utf-8")
        self.readme.unlink()
        try:
            self.readme.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"Creating symlinks is unavailable: {type(exc).__name__}")
        with self.assertRaisesRegex(spike.SpikeError, "non-symlink"):
            spike.read_readme(self.directory)

    def test_workspace_check_rejects_traversal_and_sibling_prefix(self) -> None:
        for path in (self.workspace / ".." / "outside", self.root / "application-other"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(spike.SpikeError, "inside"):
                    spike.inside_workspace(path, self.workspace)


class LifecycleTests(WorkspaceFixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.prepare_workspace()
        self.events: list[str] = []
        self.client = create_autospec(CopilotClient, instance=True)
        self.session = create_autospec(CopilotSession, instance=True)
        self.client.get_auth_status.return_value = GetAuthStatusResponse(isAuthenticated=True)
        self.client.create_session.return_value = self.session
        self.response = response_event()
        self.session.send_and_wait.side_effect = self.successful_turn
        constructor = patch.object(spike, "CopilotClient", return_value=self.client)
        self.constructor = constructor.start()
        self.addCleanup(constructor.stop)

    @property
    def tool(self):
        return self.client.create_session.call_args.kwargs["tools"][0]

    async def successful_turn(self, prompt: str, *, timeout: float):
        result = self.tool.handler(ToolInvocation(tool_name=spike.TOOL_NAME, arguments={}))
        self.assertEqual(result.result_type, "success")
        self.assertEqual(result.text_result_for_llm, self.text)
        return self.response

    async def invoke(self, **overrides):
        arguments = dict(
            workspace=self.workspace, working_directory=self.directory,
            runtime_path=self.runtime, emit=self.events.append,
        )
        arguments.update(overrides)
        return await spike.run_spike(**arguments)

    async def test_success_uses_one_fixed_reader_and_stops(self) -> None:
        with patch.dict(os.environ, {"AZURE_CLIENT_SECRET": "not-for-runtime"}):
            result = await self.invoke(model="test-model")
        self.assertEqual(result, self.response.data.content)
        self.client.start.assert_awaited_once_with()
        self.client.create_session.assert_awaited_once()
        self.session.send_and_wait.assert_awaited_once_with(spike.PROMPT, timeout=60.0)
        self.client.stop.assert_awaited_once_with()
        self.session.abort.assert_not_awaited()
        self.client.force_stop.assert_not_awaited()
        self.assertEqual(self.readme.read_text(encoding="utf-8"), self.text)
        self.assertEqual(self.events[-1], "client_stopped")
        self.assertIn("readme_read", self.events)

        client_options = self.constructor.call_args.kwargs
        self.assertEqual(client_options["mode"], "empty")
        self.assertEqual(client_options["connection"].path, str(self.runtime))
        self.assertEqual(client_options["working_directory"], str(self.directory))
        self.assertNotIn("AZURE_CLIENT_SECRET", client_options["env"])
        for location in (client_options["base_directory"], client_options["env"]["TEMP"]):
            self.assertTrue(Path(location).is_relative_to(self.workspace))
        session_options = self.client.create_session.call_args.kwargs
        self.assertEqual(session_options["available_tools"], [f"custom:{spike.TOOL_NAME}"])
        self.assertEqual(session_options["working_directory"], str(self.directory))
        self.assertEqual(session_options["model"], "test-model")
        self.assertFalse(session_options["enable_config_discovery"])
        self.assertTrue(self.tool.skip_permission)
        self.assertEqual(self.tool.defer, "never")

    async def test_permission_handler_denies_even_read_and_other_custom_requests(self) -> None:
        await self.invoke()
        handler = self.client.create_session.call_args.kwargs["on_permission_request"]
        for request in (
            PermissionRequestRead(intention="read", path=str(self.readme)),
            PermissionRequestCustomTool(tool_name="other_tool", tool_description="other"),
        ):
            self.assertIsInstance(handler(request, {"session_id": "test"}), PermissionDecisionReject)

    async def test_events_do_not_emit_message_payload_or_reasoning(self) -> None:
        await self.invoke()
        callback = self.client.create_session.call_args.kwargs["on_event"]
        event = response_event("sensitive payload")
        event.data.reasoning_text = "private reasoning"
        callback(event)
        self.assertEqual(self.events[-1], "assistant.message")
        self.assertNotIn("sensitive payload", " ".join(self.events))
        self.assertNotIn("private reasoning", " ".join(self.events))

    async def test_rejects_malicious_tool_arguments_without_reading(self) -> None:
        async def invalid_turn(prompt, *, timeout):
            with patch.object(spike, "read_readme") as reader:
                result = self.tool.handler(ToolInvocation(arguments={"path": "../secret"}))
                reader.assert_not_called()
            self.assertEqual(result.result_type, "denied")
            return self.response
        self.session.send_and_wait.side_effect = invalid_turn
        with self.assertRaisesRegex(spike.SpikeError, "did not successfully call"):
            await self.invoke()
        self.session.abort.assert_awaited_once()
        self.client.stop.assert_awaited_once()

    async def test_reader_failure_does_not_count_as_success(self) -> None:
        async def failed_read(prompt, *, timeout):
            self.readme.write_bytes(b"\xff")
            result = self.tool.handler(ToolInvocation(arguments={}))
            self.assertEqual(result.result_type, "failure")
            return self.response
        self.session.send_and_wait.side_effect = failed_read
        with self.assertRaisesRegex(spike.SpikeError, "did not successfully call"):
            await self.invoke()

    async def test_response_without_tool_call_is_not_success(self) -> None:
        self.session.send_and_wait.side_effect = None
        self.session.send_and_wait.return_value = self.response
        with self.assertRaisesRegex(spike.SpikeError, "did not successfully call"):
            await self.invoke()

    async def test_missing_final_response_is_not_success(self) -> None:
        for response in (None, response_event(" \n")):
            with self.subTest(response=response):
                self.response = response
                with self.assertRaisesRegex(spike.SpikeError, "no final response"):
                    await self.invoke()

    async def test_detects_readme_changed_during_session(self) -> None:
        async def changing_turn(prompt, *, timeout):
            response = await self.successful_turn(prompt, timeout=timeout)
            self.readme.write_text("changed", encoding="utf-8")
            return response
        self.session.send_and_wait.side_effect = changing_turn
        with self.assertRaisesRegex(spike.SpikeError, "changed during"):
            await self.invoke()
        self.session.abort.assert_awaited_once()

    async def test_start_auth_and_create_failures_still_stop_runtime(self) -> None:
        for method, stage in (
            ("start", "startup"), ("get_auth_status", "authentication"),
            ("create_session", "session creation"),
        ):
            with self.subTest(stage=stage):
                self.client.reset_mock()
                operation = getattr(self.client, method)
                operation.side_effect = RuntimeError("upstream detail")
                try:
                    with self.assertRaisesRegex(spike.SpikeError, stage):
                        await self.invoke()
                    self.client.stop.assert_awaited_once()
                    self.session.abort.assert_not_awaited()
                finally:
                    operation.side_effect = None

    async def test_unauthenticated_runtime_never_creates_session(self) -> None:
        self.client.get_auth_status.return_value = GetAuthStatusResponse(isAuthenticated=False)
        with self.assertRaisesRegex(spike.SpikeError, "not authenticated"):
            await self.invoke()
        self.client.create_session.assert_not_awaited()
        self.client.stop.assert_awaited_once()

    async def test_constructor_failure_is_reported_without_unowned_cleanup(self) -> None:
        self.constructor.side_effect = RuntimeError("invalid runtime")
        with self.assertRaisesRegex(spike.SpikeError, "runtime construction"):
            await self.invoke()
        self.client.stop.assert_not_awaited()

    async def test_send_failure_aborts_and_stops(self) -> None:
        self.session.send_and_wait.side_effect = RuntimeError("transport failure")
        with self.assertRaisesRegex(spike.SpikeError, "README inspection"):
            await self.invoke()
        self.session.abort.assert_awaited_once()
        self.client.stop.assert_awaited_once()

    async def test_whole_operation_timeout_aborts_active_session(self) -> None:
        async def never_finishes(*args, **kwargs):
            await asyncio.Event().wait()
        self.session.send_and_wait.side_effect = never_finishes
        with self.assertRaisesRegex(spike.SpikeError, "Timed out.*README inspection"):
            await self.invoke(timeout_seconds=0.05)
        self.session.abort.assert_awaited_once()
        self.client.stop.assert_awaited_once()

    async def test_cancellation_propagates_after_cleanup(self) -> None:
        active = asyncio.Event()
        async def never_finishes(*args, **kwargs):
            active.set()
            await asyncio.Event().wait()
        self.session.send_and_wait.side_effect = never_finishes
        task = asyncio.create_task(self.invoke())
        await asyncio.wait_for(active.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.session.abort.assert_awaited_once()
        self.client.stop.assert_awaited_once()

    async def test_cancellation_during_shutdown_waits_for_runtime_stop(self) -> None:
        stopping = asyncio.Event()
        release_stop = asyncio.Event()
        stop_completed = asyncio.Event()

        async def delayed_stop():
            stopping.set()
            await release_stop.wait()
            stop_completed.set()

        self.client.stop.side_effect = delayed_stop
        task = asyncio.create_task(self.invoke())
        try:
            await asyncio.wait_for(stopping.wait(), timeout=2)
            task.cancel()
            # Let cancellation reach the task while runtime cleanup is blocked.
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release_stop.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stop_completed.is_set())
            self.client.stop.assert_awaited_once()
            self.client.force_stop.assert_not_awaited()
        finally:
            release_stop.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_stop_failure_uses_force_stop_and_reports_failure(self) -> None:
        self.client.stop.side_effect = RuntimeError("stop failed")
        with self.assertRaisesRegex(spike.SpikeError, "Graceful runtime shutdown failed"):
            await self.invoke()
        self.client.force_stop.assert_awaited_once()

    async def test_shutdown_timeout_uses_force_stop(self) -> None:
        async def never_stops():
            await asyncio.Event().wait()
        self.client.stop.side_effect = never_stops
        with patch.object(spike, "CLEANUP_TIMEOUT", 0.01):
            with self.assertRaisesRegex(spike.SpikeError, "shutdown failed.*TimeoutError"):
                await self.invoke()
        self.client.force_stop.assert_awaited_once()

    async def test_cleanup_failure_keeps_primary_error_and_records_all_failures(self) -> None:
        self.session.send_and_wait.side_effect = RuntimeError("primary failure")
        self.session.abort.side_effect = RuntimeError("abort failed")
        self.client.stop.side_effect = RuntimeError("stop failed")
        self.client.force_stop.side_effect = RuntimeError("force failed")
        with self.assertRaisesRegex(spike.SpikeError, "README inspection") as raised:
            await self.invoke()
        notes = " ".join(raised.exception.__notes__)
        self.assertIn("Session abort failed", notes)
        self.assertIn("Graceful runtime shutdown failed", notes)
        self.assertIn("Forced runtime shutdown failed", notes)

    async def test_invalid_paths_and_timeouts_never_construct_client(self) -> None:
        cases = [
            {"working_directory": self.root},
            {"runtime_path": self.root / "outside.exe"},
            {"working_directory": self.workspace / "missing"},
            {"runtime_path": self.workspace / "missing.exe"},
        ]
        cases.extend({"timeout_seconds": value} for value in (0, -1, float("nan"), float("inf")))
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(spike.SpikeError):
                    await self.invoke(**arguments)
        self.constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
