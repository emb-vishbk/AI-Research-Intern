"""AI Research Intern: offline research, localhost mission control, and an opt-in Copilot spike."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
from pathlib import Path


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return seconds


def nonnegative_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    readiness = subcommands.add_parser("readiness", help="Inspect local live-execution gates without service calls")
    readiness.add_argument("--json", action="store_true")
    serve = subcommands.add_parser("serve", help="Open the local research workspace")
    serve.add_argument("--port", type=int, default=8000)
    from research_intern.execution.simulated import SCENARIOS

    simulation = subcommands.add_parser("simulate", help="Run one offline execution/evaluation/ledger demo")
    simulation.add_argument("--scenario", choices=SCENARIOS, default="improve")
    handoff = subcommands.add_parser("handoff-demo", help="Demonstrate research memory, stopping, and fresh mock proposals")
    handoff.add_argument("--outcome", choices=("regress", "runtime-failure", "goal"), default="regress")
    handoff.add_argument("--max-experiments", type=nonnegative_integer, default=3)
    candidate = subcommands.add_parser("candidate-demo", help="Validate a contract, edit and commit a candidate entirely offline")
    candidate.add_argument("--scenario", choices=("allowed", "protected", "regress"), default="regress")
    from research_intern.ledger.preparations import LOOP_SCENARIOS

    run = subcommands.add_parser("run", help="Start a bounded offline research run; no authentication or live calls")
    run.add_argument("--max-experiments", type=nonnegative_integer, default=4)
    run.add_argument("--max-attempts", type=nonnegative_integer, help="Total preparation limit; defaults to twice the experiment limit")
    run.add_argument("--scenario", choices=LOOP_SCENARIOS, default="mixed")
    run.add_argument("--steps", type=nonnegative_integer, help="Pause after N controller steps for inspection (N must be positive)")
    resume = subcommands.add_parser("resume", help="Reconcile and continue an existing offline run")
    resume.add_argument("run_directory", type=Path)
    resume.add_argument("--steps", type=nonnegative_integer)
    for name in ("status", "stop"):
        command = subcommands.add_parser(name, help=f"{name.capitalize()} an existing offline run")
        command.add_argument("run_directory", type=Path)
        command.add_argument("--json", action="store_true")
    spike = subcommands.add_parser("spike", help="Read one fixture README using Copilot")
    spike.add_argument("--live", action="store_true", help="Allow a real Copilot session")
    spike.add_argument("--working-directory", required=True, type=Path)
    spike.add_argument("--runtime-path", required=True, type=Path,
                       help="Path to the explicitly provisioned Copilot runtime executable")
    spike.add_argument("--timeout", type=positive_seconds, default=60.0,
                       help="Deadline for startup, authentication, and the turn (default: 60s)")
    spike.add_argument("--model", help="Optional model ID; otherwise use the runtime default")
    args = parser.parse_args(argv)
    workspace = Path(__file__).resolve().parents[2]
    if args.command == "readiness":
        from research_intern.controller.readiness import project_readiness
        from research_intern.workspace.project import ProjectStore

        result = project_readiness(ProjectStore(workspace).inspect())
        if args.json:
            print(json.dumps(result, indent=2, allow_nan=False))
        else:
            print("Live execution: " + ("ready" if result["can_start"] else "blocked"))
            for item in result["checks"]:
                print(f"{'CHECKED' if item['ready'] else 'BLOCKED'} {item['code']}: {item['message']}")
        return 0 if result["can_start"] else 1
    if args.command == "serve":
        if not 1 <= args.port <= 65535:
            parser.error("--port must be between 1 and 65535")
        try:
            from research_intern.api.server import serve_workspace
        except ImportError:
            print('The dashboard needs the optional web dependencies. Install this project with pip install -e ".[web]".',
                  file=sys.stderr)
            return 1
        return serve_workspace(workspace, args.port)
    if args.command in ("run", "resume", "status", "stop"):
        from research_intern.domain.experiments import SliceError
        from research_intern.offline import initialize_run, inspect_run, print_status, resume_run

        if getattr(args, "steps", None) == 0:
            parser.error("--steps must be positive")
        try:
            if args.command in ("status", "stop"):
                status = inspect_run(workspace, args.run_directory, stop=args.command == "stop")
                print_status(status, as_json=args.json)
            else:
                root = (initialize_run(workspace, max_experiments=args.max_experiments,
                                       max_attempts=args.max_attempts, scenario=args.scenario)
                        if args.command == "run" else args.run_directory)
                print(f"Offline run directory: {root}", flush=True)
                print_status(asyncio.run(resume_run(workspace, root, steps=args.steps)))
        except (SliceError, OSError, sqlite3.Error, ValueError) as exc:
            print(f"Offline run needs attention: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Offline run interrupted. Use resume with its printed directory to reconcile and continue.", file=sys.stderr)
            return 130
        return 0
    if args.command == "candidate-demo":
        from research_intern.domain.experiments import SliceError
        try:
            from research_intern.candidate_demo import run_candidate_demo
        except ImportError:
            print("The candidate demo needs PyYAML in the selected Python environment.", file=sys.stderr)
            return 1
        try:
            root, state = asyncio.run(run_candidate_demo(workspace, scenario=args.scenario))
        except (SliceError, OSError, sqlite3.Error) as exc:
            print(f"Candidate demo failed: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Candidate demo interrupted; inspect the retained workspace and ledger before continuing.", file=sys.stderr)
            return 130
        print(f"Ledger: {root / 'ledger.sqlite3'}")
        print(f"Remaining experiment slots: {state.budget.remaining_experiments}")
        print("Synthetic evidence only. Copilot usage: 0; Azure GPU-hours: 0.")
        return 0
    if args.command == "handoff-demo":
        from research_intern.domain.experiments import SliceError
        from research_intern.handoff_demo import run_handoff_demo

        try:
            root, state = asyncio.run(run_handoff_demo(workspace, outcome=args.outcome,
                                                       max_experiments=args.max_experiments))
        except (SliceError, OSError, sqlite3.Error) as exc:
            print(f"Handoff demo failed: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Handoff demo interrupted; committed ledger history is retained.", file=sys.stderr)
            return 130
        print(f"Remaining experiment slots: {state.budget.remaining_experiments}")
        if state.blocking_reasons:
            print("Next proposal blocked: " + ", ".join(state.blocking_reasons))
        else:
            print("Next proposal saved for inspection; it has not been executed.")
        print(f"Research state: {root / 'research_state.json'}")
        print("Synthetic evidence only. Copilot usage: 0; Azure GPU-hours: 0.")
        return 0
    if args.command == "simulate":
        from research_intern.domain.experiments import SliceError
        from research_intern.execution.demo import run_demo
        try:
            root, record, best = run_demo(workspace, args.scenario)
        except (SliceError, OSError, sqlite3.Error) as exc:
            print(f"Simulation failed: {exc}", file=sys.stderr)
            return 1
        print(f"SIMULATED {record.experiment_id}: {record.decision}; best: {best}")
        print(f"Ledger: {root / 'ledger.sqlite3'}")
        if record.failure_type:
            print(f"Failure category: {record.failure_type}")
        print("Synthetic evidence only. Copilot usage: 0; Azure GPU-hours: 0.")
        return 0
    if not args.live:
        parser.error("the spike requires --live because it uses real Copilot resources")

    # This first milestone is run from an editable installation in this workspace.
    try:
        from research_intern.copilot.spike import SpikeError, run_spike
    except ImportError:
        print("Copilot SDK is unavailable. Install this project in its virtual environment.",
              file=sys.stderr)
        return 1

    try:
        result = asyncio.run(run_spike(
            workspace=workspace,
            working_directory=args.working_directory,
            runtime_path=args.runtime_path,
            timeout_seconds=args.timeout,
            model=args.model,
        ))
    except SpikeError as exc:
        print(f"Spike failed: {exc}", file=sys.stderr)
        for note in getattr(exc, "__notes__", ()):
            print(note, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Spike interrupted; runtime cleanup was requested.", file=sys.stderr)
        return 130

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
