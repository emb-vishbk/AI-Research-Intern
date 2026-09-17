# AI Research Intern — Current State and Session Handoff

Updated: 2026-09-17

This file summarizes implementation progress and decisions for a new coding session. It is a handoff snapshot, not a replacement for the authoritative project documentation. Inspect the repository before continuing; this snapshot can become stale.

## Latest implementation update (2026-09-17)

The user authorized implementation and offline tests, with human-led Copilot
authentication and Azure setup. The uploaded Penn-Fudan source already exists at
`.runtime/research-project/repository`; upload is not pending. Its historical audit
explicitly rejects the old Azure output as accepted EXP-000: no exact submitted
commit, changed source, scientific approval/limits pending and no independent score
recomputation. Preserve source and archived evidence; do not relabel or freeze them.

Added `copilot/files.py`, `copilot/live.py`, `execution/azure.py`,
`ledger/services.py` and `controller/readiness.py`. These provide restricted fresh
coding turns, exact-source Azure staging, durable submission/turn reservations,
ambiguous-submission reconciliation and explicit dashboard readiness checks.
The optional `azure` dependency group is declared but not installed in this pass.
**Live adapters are not wired into the existing simulation-only ledger/loop.**
Baseline acceptance, trusted scoring, real controller/UI composition, provider
billing enforcement and the measured autonomous demonstration remain unfinished.

The selected Windows `.venv` lacked declared YAML/web-test dependencies; these were
installed locally to run tests. No runtime/browser download, service sign-in,
Copilot call, Azure job, source-workload change or application Git commit occurred.
Verification also exposed Windows Git path/newline handling and test connection
cleanup issues; these were corrected without relaxing protected-path checks.
See the README's live integration status for human setup and acceptance gates.

## Prior local-setup snapshot (superseded where noted above)

The researcher has explicitly said the optimization metric and evaluation protocol
are **still undecided**. They will upload the repository and review the metric with
the coding agent afterward. Older mAP/model/dataset choices below are historical
proposals, not authority to configure an active experiment. Copilot integration is
also deferred pending a later decision. Do not request new service sign-ins or
invoke Copilot/Azure as part of this local setup work.

Source upload now accepts a source-only folder even without a contract, for review.
A supplied contract must validate. `POST /api/project/prepare` stages an independent
Git repository, checks all source without executing it, and records its source commit,
contract and file inventory in `workspace.json`. It preserves source bytes and never
adopts uploaded Git metadata, force-adds ignored content or resets unexpected work.
Interrupted Git publication and later source drift require inspection.

The optional contract `evaluation` section pins the evaluator entrypoint and fixed
split manifest using SHA-256 and records the metric definition, dataset version and
procedure. Those paths are automatically protected. The UI displays the complete
policy and offers explicit confirmation against the source commit, contract digest
and evaluation fingerprint. The existing output collector rejects missing/mismatched
fingerprints before deterministic scoring when a protocol is configured. This is a
consistency gate, not proof of actual trusted workload execution.

No measured baseline is created by preparation. Live Start, service adapters and
compute/credit enforcement remain unavailable. Draft budget controls are labelled
accordingly. No new dependency or provider-specific research implementation was added.
See `README.md` for the local UI workflow and `Docs/EXPERIMENT_CONTRACT.md` for fields.

Validation on 2026-09-17: all 166 offline slice tests passed across split runs (the
first long run reached its command time limit after 127 passing checks; the remaining
39 completed successfully). Coverage includes real local Git preparation, unchanged
source, ignored/private files, protected evaluation inputs, explicit confirmation,
controller reuse, output fingerprint rejection, restart/stop behavior, upload/API
boundaries and a real loopback HTTP/SSE server. JSON schemas and JavaScript syntax
were checked. The Linux web environment lacks the Copilot SDK, so SDK-only tests
were excluded; no new package, browser or runtime was downloaded. Browser rendering
remains unverified because the earlier browser download was declined. No service
sign-in, live agent/cloud call, real ML execution or application Git commit occurred.

## 1. Workspace and project objective

Workspace: `/mnt/e/AI Research Intern`, also accessible as `/mnt/e/ai research intern` in the current environment.

AI Research Intern is an autonomous system for improving an existing computational ML experiment through repeated, externally evaluated, traceable experiments.

```text
Human defines experiment, objective, boundaries, budgets, and baseline
→ controller selects parent experiment
→ fresh Copilot session proposes and modifies code
→ deterministic validation checks changes
→ Git records candidate
→ Azure ML executes
→ protected evaluator calculates results
→ ledger records evidence
→ research state summarizes learning
→ repeat until stopped
```

Copilot proposes. Deterministic software owns permissions, Git, execution, scoring, budgets, persistence, and stopping.

Keep three systems distinct:

1. AI Research Intern application.
2. Researcher's experiment repository.
3. Azure ML execution environment.

Build a small modular monolith, one complete slice at a time. The user wants each addition to fit the final research loop coherently. Do not expand MVP scope or replace this clean application with a cloned external codebase.

## 2. Documentation to read first

Read [AGENTS.md](AGENTS.md), [README.md](README.md), and all five Markdown files in `Docs/` before substantial implementation.

Resolve documentation conflicts in this order:

1. [MVP_SCOPE.md](Docs/MVP_SCOPE.md)
2. [ARCHITECTURE.md](Docs/ARCHITECTURE.md)
3. [RESEARCH_LOOP.md](Docs/RESEARCH_LOOP.md)
4. [EXPERIMENT_CONTRACT.md](Docs/EXPERIMENT_CONTRACT.md)
5. [PROJECT.md](Docs/PROJECT.md)
6. [README.md](README.md)

Some initial-project-state statements in `AGENTS.md` predate the implementation. Inspect actual code and the implementation sections of `README.md` before deciding what already exists. Surface meaningful conflicts rather than silently redesigning the project.

## 3. Implemented so far

| Component | Current implementation |
| --- | --- |
| Python package and CLI | `src/research_intern/`, Python >=3.11, and commands `spike`, `simulate`, `handoff-demo`, `candidate-demo`, `run`, `resume`, `status`, `stop`, and `serve`. Core dependencies remain Copilot SDK and PyYAML; optional `web` dependencies add FastAPI/Uvicorn, and `web-test` adds HTTPX. |
| Copilot spike | An explicit `--live` command for one controlled README-reading session. Includes narrow tool permissions, authentication checks, timeouts, and cleanup. No successful live session has been validated. |
| Execution interface | Narrow submit/status/download/cancel operations and a persistent simulated executor. The execution controller currently accepts simulated execution only. |
| Output validation | Strict validation of standardized JSON outputs, experiment identity, metrics, and required directories. Schemas exist for `run.json` and `metrics.json`. |
| Deterministic evaluation | Supports maximize/minimize objectives, targets, and min/max constraints. Records `KEEP`, `REJECT`, `FAILED`, or `GOAL_REACHED`, distinguishing poor model quality from execution/output failures. |
| Experiment ledger | SQLite stores experiment IDs, parent/commit/job references, metrics, decisions, failures, and lifecycle events. Files store outputs and diffs. Terminal records are immutable. |
| Research-state handoff | Rebuilds compact research memory from ledger history, distinguishes best/latest/selected parent, retains negative evidence, and supplies context to a fresh scripted proposer. |
| Stopping and budgets | Persistent experiment-count and preparation-attempt limits plus human stop. The loop reconciles unfinished work before checking whether new work can start. |
| Local Git fixtures | Disposable dedicated repositories with real baseline/candidate commits for offline demonstrations. Arbitrary repository onboarding remains deferred. |
| Experiment contract | Strict YAML loader and `schemas/contract.schema.json` cover the core objective, execution configuration, output paths, editable/protected scope, constraints, and budgets. The effective contract and dedicated repository identity are persisted before baseline import. |
| Controlled candidate workflow | Restores selected-parent code in a dedicated offline repository, invokes a fresh mock editor, checks actual filesystem/Git changes, validates syntax, commits exact files, reserves the candidate, and connects to the existing execution pipeline. Protected changes block execution. |
| Offline research loop | Repeats candidate preparation, simulated execution, evaluation and research-state handoff until goal, experiment limit, preparation limit, or human stop. Default demo covers improvement, rejection, runtime failure, then goal. |
| Basic recovery | SQLite preparation journal, atomic reservation linkage, process lock, archived failed edits, validated commit recovery, and reuse of pending experiment/job IDs. CLI resumes initialized offline runs and refuses to erase unknown/newer work. |
| Portable researcher guide | `RESEARCH_INTERN_REQUIREMENTS.md` can be copied alone into an independent ML repository. It covers standalone operation, task-agnostic contract/output requirements, protected evaluation, provenance, baseline handoff, proposed CLI/Azure bindings, and current platform limitations. |
| Localhost API and browser | Thin FastAPI transport, one serial background offline driver, server-sent snapshots, run controls, objectives/budgets, experiment history and detailed evidence. Plain locally served HTML/CSS/JavaScript; no Node build or browser sign-in. |
| Fixed research directory | `.runtime/research-project/repository/` is the agreed place for a copy of the independent ML repository. The browser checks its contract, scope paths and job YAML; explicit local preparation now creates its controlled Git workspace. Measured baseline import and actual ML execution remain pending. |

Main module boundaries:

```text
src/research_intern/
├── main.py                   CLI entry point
├── handoff_demo.py           offline continuity demonstration
├── offline.py                offline run setup and CLI composition
├── controller/              candidate, execution, handoff, serial research loop, mission service
├── api/                     optional FastAPI transport + packaged browser assets
├── contracts/               immutable core contract + strict YAML loader
├── copilot/                 SDK spike, proposer interface, scripted proposer
├── domain/                  experiment, state, and candidate-plan data models
├── execution/               executor interface, simulations, output collection
├── evaluation/              deterministic decisions
├── ledger/                  SQLite history, preparation journal, state reconstruction
├── validation/              Python/JSON/YAML preflight without code execution
└── workspace/               path checks, controlled Git, recovery, process locks, fixtures
```

The main integration interfaces are:

```text
Proposer.run_iteration(context, working_directory) → CandidatePlan
Executor.submit_job / get_status / download_outputs / cancel_job
ExecutionController → output validation → evaluator → Ledger
Ledger snapshot → build_research_state → HandoffController
Ledger snapshot + full contract → CandidateController.prepare_candidate → PREPARED record
PREPARED record → ExecutionController.resume_submission / advance
ResearchLoop.run / step → reconcile persisted work → prepare / execute / repeat or stop
```

The proposer interface is independent of SDK imports. The original offline slices
use the standard library and Git; the candidate slice also uses the already
available PyYAML 6.0.1. No download or authentication was needed to build this slice.

## 4. What the demonstrations prove

### Execution → evaluation → ledger

`simulate` connects a fixture candidate commit to simulated execution, output validation, deterministic evaluation, and a ledger record. Scenarios cover improvement, regression, constraint violation, goal reached, runtime failure, invalid output, and submission failure.

The default synthetic baseline has F1 `0.80`, the candidate has F1 `0.84`, the target is `0.87`, and the latency constraint is `<= 50 ms`. These values exercise software behavior; they are not measured ML results.

### Experiment-to-experiment handoff

`handoff-demo` records an improvement followed by a regression, failure, or goal. After a regression, the previous best remains the selected parent while the rejected experiment remains the latest result and part of research memory.

The handoff produces a subsequent scripted plan. It does not execute an unrestricted autonomous loop.

### Contract → controlled candidate → execution

`candidate-demo` creates a prepared experiment with a protected evaluator and
annotations, loads and persists its contract, then makes two real file edits with
fresh mock editors. Each edit is validated, committed, executed synthetically,
evaluated, and recorded. After the second result regresses, the actual workspace
returns to the earlier best commit. `--scenario protected` demonstrates that an
evaluator modification is blocked before reservation or execution; all three
experiment slots remain available. `--scenario allowed` runs one permitted edit.

All proposals and ML scores in these demonstrations are synthetic. No actual ML improvement, live Copilot research iteration, or Azure experiment has occurred.

### Bounded repetition and restart

`run` creates an initialized offline loop with a full contract and synthetic baseline.
The default four candidates produce `KEEP`, `REJECT`, `FAILED`, and `GOAL_REACHED`.
After negative outcomes the best eligible commit remains the next parent. `--steps N`
pauses the foreground controller; `resume` reconciles persisted work in that same run.
`status --json` exposes rebuilt research memory and budgets. `stop` persists a human
stop and is usable from another terminal while the driver is active.

`invalid-first`, `invalid-always`, and `protected-first` scenarios exercise bounded
preparation failures and restoration. These are scripted software tests, not claims
that the research agent learned or improved a model.

## 5. Important behavior and limitations

- `EXP-000` is the human baseline. Candidate IDs are unique within a run and must not be reused.
- Reserving a candidate consumes one experiment slot, including subsequent submission or execution failures. Baseline imports and draft proposals consume none.
- Offline loop preparation attempts have a separate immutable cap (`--max-attempts`, default twice `--max-experiments`). Each starts before editing; failures and interruptions count. This is application configuration, not a new contract field. Resume cannot reset either count.
- Experiment limits are fixed before baseline import and persist across reopening the ledger. Legacy runs without a configured limit remain readable but cannot start new candidates.
- Compute and Copilot account budget accounting remain unimplemented. Unknown remaining allowances are represented as unknown, not as measured available credits.
- Job IDs are persisted before polling. `resume` handles initialized offline loops: it resumes reserved candidates and existing jobs, and retries transient collection failures. An interrupted `SUBMITTING` state attaches a matching durable simulated job or records submission failure when no job exists, without resubmission. Live Azure reconciliation remains pending.
- A persisted human stop blocks new work. Already submitted jobs can still be collected; stopping does not currently imply remote cancellation.
- Research state is rebuilt from ledger history. Exported `research_state.json` files are reviewable snapshots, not the authoritative memory source.
- The original handoff remains plan-only. `CandidateController` prepares the actual selected-parent code before editing and supplies the full persisted contract.
- `ExecutionController` accepts prevalidated candidates; the new candidate controller now supplies them for dedicated offline repositories. Arbitrary researcher-repository onboarding remains deferred.
- Full core YAML contract loading, editable/protected scope and configurable output paths are implemented. The Azure job file is parsed locally; service validation and execution remain pending. Optional shell smoke commands are unsupported.
- Output collection requires configured run, metrics, history, logs and artifacts paths; defaults remain `run.json`, `metrics.json`, `metrics_history.json`, `logs/`, and `artifacts/`. Both directories may be empty.
- Live proposers and live execution adapters remain disabled in the offline controller paths.
- The dedicated repository must live inside its run under `.runtime/`. The controller verifies ordinary SHA-1 Git configuration and refuses worktrees, submodules, attributes, symlinks/hardlinks, unversioned empty directories, dirty work and unrecorded HEAD commits. Fixture limits are 16 MiB per file, 128 MiB per scanned tree and 10000 entries. This is not an OS sandbox or large-data onboarding.
- Git control files, `.research_intern/`, the Azure job configuration and generated output root are automatically protected. Validation inventories include ignored and untracked files. Git metadata changes are rejected.
- Candidate plans/checks/failures persist in `candidate_attempts/`; successful commit evidence is written in `candidates/` before reservation and copied into the experiment directory. Durable Git refs retain rejected/interrupted candidates. The loop archives matching failed edits under `rejected_files/` before restoring the recorded parent. The standalone candidate demo still leaves failures in place.
- SQLite preparations move through `PREPARING → EDITING → COMMITTING → COMMITTED → RESERVED`, or `FAILED → RECOVERING → RECOVERED`. Reservation and its journal linkage share one transaction. Resume finishes a journaled validated commit or evidence publication without invoking the editor again or assigning another ID.
- `operation.lock` is an OS advisory lock held over a managed driver; it releases on process exit. A known stale `candidate.lock` marker can then be removed. Unknown markers, Git tampering, newer edits, and abruptly interrupted dirty edits without a final inventory require inspection. Recovery never blindly resets or cleans a dirty repository. These checks are not an OS sandbox.
- `run`/`resume` are foreground commands, not background workers. Status reports the last persisted controller observation, not a liveness guarantee. Human stop is permanent for that run; submitted jobs can still be collected, but prepared candidates cannot submit. Incomplete baseline setup and legacy demo migration remain outside automatic recovery.
- `serve` adds one web-owned background thread with its own SQLite connection; request reads use separate connections. An application server lock prevents two web servers in the workspace, and existing per-run locks prevent duplicate CLI/web drivers. Graceful server shutdown interrupts the driver without setting human stop. Restart lists existing runs and requires explicit resume. The UI identifies web-driver activity separately from persisted controller state.

## 6. Verification and useful commands

The latest completed verification passed **135 offline/API/server tests**:

- 34 execution/evaluation/ledger tests in `tests/test_execution_slice.py`.
- 26 handoff/state/stopping tests in `tests/test_handoff_slice.py`.
- 33 contract/candidate/permission/Git tests in `tests/test_candidate_slice.py`.
- 29 repetition/preparation/job-recovery/stop/locking/CLI tests in `tests/test_loop_slice.py`.
- 12 API/driver/contract-readiness tests in `tests/test_api_slice.py`.
- 1 real loopback HTTP/static-asset/server-sent-event test in `tests/test_server_slice.py`.

The API tests verified the full improve/reject/fail/goal journey, permanent stop,
collection of an existing job after stop, failed-preparation recovery, one active
driver, shutdown interruption, and reuse of a persisted job after restart. The
HTTP smoke test started and stopped a temporary Uvicorn server successfully. All
four prior offline suites passed again. The final shutdown adjustment was checked
again with the server smoke test. Browser element references and Python/TOML syntax
were checked; no headless browser was installed, so visual and interactive browser
verification remains outstanding. SDK/live-service tests remain unverified.

Another 31 CLI/spike tests exist: five in `tests/test_main.py` and 26 in `tests/test_spike.py`. These were not executed because the selected Linux Python environment does not have the Copilot SDK. No SDK installation was attempted. Do not describe the entire test suite or live integrations as verified.

The loop implementation reran all three existing offline suites and added fault
injection around editing, Git staging/commit, reservation, submission, and output
collection. It also tested refusal to erase newer work, process-exit lock release,
bounded retries, and persistent stop. All source/test Python files passed syntax
parsing. Contract schemas are unchanged from the previously validated candidate slice.

The 2026-09-14 researcher guide introduces no runtime changes. Its three YAML and
four JSON examples were checked against the existing schemas, the real contract
loader with temporary repository paths, the output collector, baseline evaluation,
and failure classification. The Azure example is a placeholder template aligned
with official command-job documentation; no Azure resource or execution was validated.

From the workspace, using Linux/WSL Python:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -p 'test_*slice.py' -v
PYTHONPATH=src python3 -B -m research_intern.main simulate
PYTHONPATH=src python3 -B -m research_intern.main handoff-demo
PYTHONPATH=src python3 -B -m research_intern.main handoff-demo --outcome runtime-failure --max-experiments 2
PYTHONPATH=src python3 -B -m research_intern.main handoff-demo --outcome goal
PYTHONPATH=src python3 -B -m research_intern.main candidate-demo
PYTHONPATH=src python3 -B -m research_intern.main candidate-demo --scenario protected
PYTHONPATH=src python3 -B -m research_intern.main run
PYTHONPATH=src python3 -B -m research_intern.main run --scenario invalid-first --max-experiments 1 --max-attempts 2
PYTHONPATH=src python3 -B -m research_intern.main run --steps 1
# Replace RUN_DIRECTORY with the path printed by run:
PYTHONPATH=src python3 -B -m research_intern.main status RUN_DIRECTORY --json
PYTHONPATH=src python3 -B -m research_intern.main resume RUN_DIRECTORY
PYTHONPATH=src python3 -B -m research_intern.main stop RUN_DIRECTORY
```

Tests and demos create artifacts under `.runtime/`. A CLI run with an invalid first
candidate recovered and completed within its two-attempt cap. A separate run paused
at `PREPARED`, reopened through CLI `resume`, and completed all four default outcomes;
CLI `status --json` showed its full persisted evidence and zero live resource usage.

A Windows `.venv` also exists. Linux Python can run the offline slices without installing the Copilot SDK. The SDK runtime was provisioned earlier under `.runtime/copilot-sdk/`; it should not be downloaded again automatically.

## 7. Completed slice and next recommended build

Windows CMD launch is available via `start-web.cmd` in the platform root. It runs
the existing Linux web environment through the default WSL distribution without
opening an interactive WSL shell. Ctrl+C in that CMD window stops the server.
`serve` now bounds connection draining to three seconds so open browser SSE
streams cannot indefinitely delay driver cleanup. No dependencies were added.
The CMD wrapper has not been executed from Windows in this session.

**The offline research loop, basic recovery and localhost mission control are implemented.** The loop composes the
existing controlled candidate and execution paths into bounded serial repetition,
adds persisted preparation attempts and safe recovery, and exposes run/status/
resume/stop commands. The original simulation, handoff and candidate demos remain
available. The loop itself needed no new dependency; the optional web transport
adds FastAPI/Uvicorn and HTTPX for API tests.

The portable [researcher requirements guide](RESEARCH_INTERN_REQUIREMENTS.md) and
task-specific [build prompt](prompt.md) are ready to copy into the separate ML
workspace. The prompt records the locked pedestrian-detection task and proposes
explicit development defaults; numerical budgets and final experiment policy remain
unresolved. Following the Azure clarification, the prompt now requires a one-job
operator helper and a completed real Azure baseline with resumable output collection
and verified downloads. A YAML template alone is an intermediate milestone. The
guide stays task agnostic, and the experiment must also run without the platform.

The next useful milestone is to inspect the researcher's uploaded source and agree
on the objective, metric scale, evaluator, immutable data/split, allowed interventions
and resource limits. Section 8 records earlier workload proposals only; the user
has reopened the metric decision. Keep the workload separate from the platform.
Data/model acquisition and measured training have not been performed here.

The localhost API/browser now exposes the offline loop. Start it from this
workspace using:

```bash
PYTHONPATH=src .runtime/web-venv/bin/python -B -m research_intern.main serve
```

Open `http://127.0.0.1:8000`. Upload a source-only copy of the independent research
repository. A valid core contract enables local Git preparation; an explicit pinned
evaluation protocol enables review/confirmation. The fixed source location remains
`.runtime/research-project/repository/`. Source setup is not baseline import. The
browser does not expose developer simulation creation controls; those remain in the
CLI/API. The prepared workload and measured baseline handoff still require evidence.

After the researcher chooses the agent integration, validate editing and one real Azure candidate,
with compute/AI budget enforcement and live submission reconciliation, before
running the loop on measured ML experiments. Live integration must replace explicit
simulated-mode guards and ledger restrictions deliberately; it is not just an adapter swap.

## 8. Historical pedestrian-detection proposal — not an active objective

The following records an earlier proposal for the independent autolabelling workspace.
The latest user instruction explicitly leaves the metric and evaluation protocol
undecided until repository review. Do not treat this table or the recommendations
below as approval to configure a real experiment.

| Decision | Earlier proposal |
| --- | --- |
| Dataset | Penn-Fudan Pedestrian |
| Model family | TorchVision Faster R-CNN |
| Task | Image → pedestrian bounding boxes, with confidence scores |
| Primary objective | Maximize validation bounding-box mAP@0.50:0.95 |
| Baseline | `EXP-000`: a simple, legitimate fit using a fixed train/validation split, basic training loop and simple hyperparameters |

Use the [official tutorial implementation](https://github.com/pytorch/tutorials/blob/main/intermediate_source/torchvision_tutorial.py)
as the workload starting point, with the required training/COCO evaluation helpers
from [pytorch/vision/references/detection](https://github.com/pytorch/vision/tree/main/references/detection).
If this proposal is approved again, adapt its Faster R-CNN example for the bbox task; the tutorial's full training
example uses Mask R-CNN. Keep the detector as a TorchVision dependency, pin copied
source revisions, preserve notices, and add the CLI, fixed policy, standardized exports
and one-job Azure workflow around the reused code. Keep scoring dependencies protected
when separating training and evaluation. The prompt records this reuse strategy;
the portable requirements guide remains task agnostic.

The optimization problem is:

```text
maximize over θ ∈ S: f(θ) = validation bbox mAP@0.50:0.95
subject to fixed per-trial training-time and peak-memory limits
and a bounded total experiment/compute budget.
```

Here θ denotes the permitted candidate code/configuration intervention. Its score
comes from training Faster R-CNN under that recipe and evaluating actual predictions;
θ is not a free choice of learned weights or reported metrics. `S` is the
human-defined intervention space enforced by protected policy and validation.

**Proposed initial intervention space, to finalize before baseline measurement:**
learning rate, optimizer, scheduler/warmup, weight decay, training-only box-aware
augmentation, batch size and backbone freezing/unfreezing. Fix the exact model
variant/backbone, initialization checkpoint, training duration policy, seed protocol,
data membership and evaluation/inference protocol across comparisons. Each trial
should start from the same approved initialization; selecting parent code does not
implicitly permit continuing training from its learned checkpoint. Use the same
resource ceilings for baseline and candidates. Pin parameter ranges in protected
workload policy; the generic platform schema does not itself validate these ranges.

Protect dataset/annotation and split manifests, label mapping, metric implementation,
evaluation settings, trial/output wrapper, dependencies, contract and budgets. No
extra training data, alternative detector, ensemble, evaluation changes or final-test
access belongs in this proposed initial search space. Retain a separate final-test
partition outside candidate development for the final audit; iterate on validation.

Recommended metric key: `val_map_50_95`, scale 0–1, using pedestrian bounding-box AP
averaged over IoU thresholds 0.50, 0.55, …, 0.95 under a pinned
[COCO evaluation protocol](https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/cocoeval.py).
Freeze recall interpolation, detection limits and area settings too. AP50 and AP75
can be diagnostics; the primary metric remains unchanged throughout research.

The baseline should be untuned and correct, with no deliberate label corruption,
broken preprocessing or fabricated low score. Measure its quality and cost first.
Headroom is an empirical question: if the simple fit is already strong, report that
and revisit the setup with the researcher before freezing a run. Do not weaken an
established baseline or change the split to manufacture improvement.

Still unresolved: exact model variant/checkpoint, split membership, baseline recipe,
allowed parameter ranges, training/measurement protocol, numerical resource limits,
experiment allowance and optional improvement target. These are not active contract
values. The current importer requires `EXP-000` to satisfy all hard constraints.
Report workload timing/memory through trusted measurement; live compute enforcement
and platform GPU/AI accounting still need implementation.

No real dataset/model acquisition, baseline measurement or live platform integration
is established by this decision. Keep exact code/data/configuration/artifact lineage,
retain negative results and repeat promising stochastic comparisons under an agreed
seed protocol. The workload and trusted evaluator belong in the separate experiment
repository, connected through the existing contract and standardized outputs.

## 9. Workspace safety and deferred integrations

This is an organization-managed laptop. The user authorized offline infrastructure development while deferring authentication and permissions. Keep development and artifacts within the workspace, avoid unnecessary downloads or external operations, and do not initiate live Copilot/Azure calls without the required authorization.

A previous request to run Windows Python outside the sandbox was declined. Do not bypass that decision. Linux Python was sufficient for the offline slices.

Runtime/test paths use `.runtime/`, which is ignored by Git. Fixture Git commands use isolated subprocess settings and do not change machine Git configuration. Workspace path checks are application safeguards, not an OS sandbox; do not promise that these checks alone prevent all filesystem access or network egress.

The original design proposed invoking runtime Copilot through its SDK, not by automating VS Code Chat, using the user's existing company entitlement. The current user has deferred this integration decision. Authentication, organizational access, and billing applicability remain unverified. A VS Code sign-in alone has not validated the isolated runtime's authentication. Azure compute is a separate resource budget.

Never put credentials in source, contracts, prompts, research state, ledger records, Git, or chat.

## 10. Development Git state and handoff scope

At inspection, the application repository had **no commits on `master`**, and the project files were untracked. Preserve existing work; do not stage, commit, reset, or push the application repository unless instructed. Disposable experiment repositories contain their own fixture commits and must not be confused with the application repository.

`README.md`, `Docs/ARCHITECTURE.md`, `Docs/RESEARCH_LOOP.md`, and `Docs/EXPERIMENT_CONTRACT.md` were updated alongside earlier implementation slices. An earlier generated workflow image is under `output/`; it is not executable functionality or an authoritative implementation specification.

The latest user direction accepted a fixed local research directory as the simplest
onboarding arrangement while the independent ML repository develops in parallel.
The platform now has an offline API/browser slice around the existing controller.
Source upload and explicit Git preparation are available; live research integration is not enabled.

After approval, the Python installer and optional web/test packages were downloaded
into ignored `.runtime/` storage, using an isolated Linux `.runtime/web-venv` with
system PyYAML available. No system package installation, service sign-in, live
Copilot/Azure call, or application Git commit was performed. The optional headless
browser download was declined; do not retry or bypass that decision. Browser
rendering and interactive JavaScript behavior remain unverified in a real browser.
API tests require execution outside this sandbox because its socket restrictions
block the test client's event-loop thread wakeup; the application is still local.
