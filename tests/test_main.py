"""CLI consent and error reporting, entirely offline."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from research_intern import main
from research_intern.copilot.spike import SpikeError


class MainTests(unittest.TestCase):
    arguments = ["spike", "--working-directory", "fixture", "--runtime-path", "runtime"]

    def test_readiness_reports_blockers_without_starting_copilot(self) -> None:
        with patch("research_intern.workspace.project.ProjectStore.inspect", return_value={}), \
                patch("research_intern.copilot.spike.CopilotClient") as client, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            status = main.main(["readiness", "--json"])
        self.assertEqual(status, 1)
        self.assertFalse(json.loads(output.getvalue())["can_start"])
        client.assert_not_called()

    def test_missing_live_flag_cannot_start_client(self) -> None:
        errors = io.StringIO()
        with patch("research_intern.copilot.spike.CopilotClient") as client:
            with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
                main.main(self.arguments)
            client.assert_not_called()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("requires --live", errors.getvalue())

    def test_invalid_timeout_cannot_run_spike(self) -> None:
        with patch("research_intern.copilot.spike.run_spike", new_callable=AsyncMock) as run:
            for timeout in ("0", "-1", "nan", "inf", "invalid"):
                with self.subTest(timeout=timeout):
                    with contextlib.redirect_stderr(io.StringIO()):
                        with self.assertRaises(SystemExit) as raised:
                            main.main([*self.arguments, "--live", "--timeout", timeout])
                    self.assertEqual(raised.exception.code, 2)
            run.assert_not_awaited()

    def test_success_prints_response_and_passes_explicit_options(self) -> None:
        output = io.StringIO()
        with patch("research_intern.copilot.spike.run_spike", new_callable=AsyncMock,
                   return_value="Verified summary.") as run:
            with contextlib.redirect_stdout(output):
                status = main.main([*self.arguments, "--live", "--timeout", "12.5",
                                    "--model", "test-model"])
        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue(), "Verified summary.\n")
        run.assert_awaited_once()
        options = run.call_args.kwargs
        self.assertEqual(options["timeout_seconds"], 12.5)
        self.assertEqual(options["model"], "test-model")
        self.assertEqual(options["working_directory"], Path("fixture"))
        self.assertEqual(options["runtime_path"], Path("runtime"))

    def test_failure_reports_cleanup_notes_without_traceback(self) -> None:
        failure = SpikeError("Unable to inspect fixture.")
        failure.add_note("Runtime cleanup failed.")
        errors = io.StringIO()
        with patch("research_intern.copilot.spike.run_spike", new_callable=AsyncMock,
                   side_effect=failure):
            with contextlib.redirect_stderr(errors):
                status = main.main([*self.arguments, "--live"])
        self.assertEqual(status, 1)
        self.assertIn("Unable to inspect fixture.", errors.getvalue())
        self.assertIn("Runtime cleanup failed.", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_keyboard_interrupt_returns_shell_interrupt_status(self) -> None:
        with patch.object(main.asyncio, "run", side_effect=KeyboardInterrupt):
            with patch("research_intern.copilot.spike.run_spike", new=lambda **kwargs: None):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main.main([*self.arguments, "--live"]), 130)


if __name__ == "__main__":
    unittest.main()
