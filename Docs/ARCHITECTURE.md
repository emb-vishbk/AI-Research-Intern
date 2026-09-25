# AI Research Intern

## ARCHITECTURE.md

## 1. Purpose

This document defines the technical architecture of the AI Research Intern MVP.
`PROJECT.md` explains the thesis; `MVP_SCOPE.md` defines the hackathon boundary; this file defines software structure, ownership, dependencies, runtime flow, and persistence.
The architecture should remain local-first, modular, deterministic around infrastructure, and simple enough for a coding agent to modify safely.
These boundaries should not be changed casually during implementation.

## 2. Core Architectural Principle

The system is a deterministic experimentation platform with an LLM-powered research and coding engine inside it.
Copilot handles open-ended reasoning, repository understanding, hypothesis generation, and controlled code modification.
Normal software owns execution, persistent state, permissions, budgets, scoring, credentials, recovery, and stopping.
Azure ML is the remote computational laboratory.
The evaluator, not the LLM, determines whether an experiment improved.

## 3. Top-Level System

```text
Researcher
↓
Browser UI / VS Code
↓
Local FastAPI Backend
↓
Research Controller
├─ Copilot Adapter
├─ Contract + Permissions
├─ Git / Workspace Manager
├─ Validation Gate
├─ Azure ML Executor
├─ Evaluator
├─ Experiment Ledger
└─ Research State Builder
```

The browser observes and controls the process; it does not own research logic.

## 4. System Boundaries

The researcher's experiment remains ordinary ML code with data access, evaluator, configs, Azure job definition, and Research Intern contract.
AI Research Intern is the application we build: controller, Copilot integration, Git lifecycle, validation, Azure integration, evaluation, memory, API, and UI.
Azure ML is the execution backend for long-running or GPU-heavy experiments.
These systems communicate through narrow interfaces and should not share responsibilities.
The experiment should not be rewritten around our framework.

## 5. Deployment Model

The MVP runs locally except for Azure compute.

```text
researcher's machine
├─ VS Code
├─ ai-research-intern/
├─ experiment Git repo
├─ local Python backend
└─ localhost browser UI
remote
└─ Azure ML
```

Copilot SDK uses the locally authenticated Copilot environment.
Azure authentication is backend-owned; no production cloud deployment is required.

## 6. Repository Strategy

Build AI Research Intern as a clean repository from scratch.
Do not clone `github/awesome-copilot` as the product base.
Use official Copilot SDK/Ralph examples only to borrow client startup, session creation, working-directory control, event handling, fresh iterations, and shutdown mechanics.
Our repository should directly represent our product architecture and avoid unrelated cookbook/example structure.
This keeps the codebase smaller and easier for Copilot to understand.

## 7. Repository Layout

```text
ai-research-intern/
├─ AGENTS.md
├─ README.md
├─ pyproject.toml
├─ docs/
│  ├─ PROJECT.md
│  ├─ MVP_SCOPE.md
│  ├─ ARCHITECTURE.md
│  ├─ RESEARCH_LOOP.md
│  └─ EXPERIMENT_CONTRACT.md
├─ src/research_intern/
│  ├─ main.py
│  ├─ api/ controller/ copilot/ contracts/
│  ├─ workspace/ validation/ execution/
│  └─ evaluation/ ledger/ domain/
├─ schemas/  ui/  tests/
```

This is a modular monolith; do not split it into services for the MVP.

## 8. Module Responsibilities

```text
api/         HTTP/WebSocket routes
controller/  outer research lifecycle + stopping
copilot/     Copilot SDK adapter, prompts, events
contracts/   contract loading + validation
workspace/   project loading, Git, diffs, permissions
validation/  preflight, smoke tests, budget checks
execution/   Azure executor + result collection
evaluation/  objective + constraints + final decision
ledger/      SQLite, artifacts, research-state builder
domain/      typed models shared across modules
```

Exact filenames may evolve, but these ownership boundaries should remain recognizable.

## 9. Dependency Direction

```text
API / UI
↓
Application Controller
↓
Domain Models
↓
Infrastructure Adapters
```

The controller composes the system.
Copilot code should not create Azure clients or SQLite connections.
Azure code should not know how hypotheses are generated.
The evaluator should not depend on Copilot SDK types.

## 10. Domain Layer

Core domain concepts are `Experiment`, `CandidatePlan`, `ExperimentContract`, `EvaluationResult`, `ResearchState`, and `BudgetState`.
Use plain Python models, preferably dataclasses or Pydantic where validation is useful.
Domain objects represent meaning, not infrastructure.
They should not perform Git, Azure, HTTP, filesystem, or database operations.
These models form the shared language between modules.

## 11. Research Controller

The Research Controller is the heart of the system and owns the outer autonomous loop.
It loads state, selects a parent, invokes Copilot, validates the candidate, versions code, submits Azure, waits, collects outputs, evaluates, records the experiment, rebuilds research state, enforces budgets, and decides whether to continue.

```text
LOAD → REASON → IMPLEMENT → VALIDATE → VERSION
→ EXECUTE → COLLECT → EVALUATE → RECORD → UPDATE → REPEAT/STOP
```

The controller is deterministic workflow software, not another LLM agent.

## 12. Controller State Machine

Use explicit lifecycle states:

```text
IDLE → LOADING_STATE → COPILOT_RUNNING → PREFLIGHT
→ READY_TO_SUBMIT → AZURE_RUNNING → COLLECTING
→ EVALUATING → RECORDING → STOPPED/FAILED
```

State transitions should be observable and persistable.
Avoid hiding the lifecycle inside deeply nested callbacks.

## 13. Copilot Adapter

All `github-copilot-sdk` usage should be isolated behind a Copilot adapter.
It handles client startup, session creation, working-directory configuration, prompt submission, SDK events, completion, structured output capture, session destruction, and shutdown.
Expose a small interface such as:

```text
run_iteration(context, working_directory) -> CandidateResult
```

Other modules should not depend heavily on SDK-specific objects.
This keeps the reasoning runtime replaceable later.

## 14. Copilot Session Model

Use a fresh Copilot session for each experiment iteration.
Each session receives objective, experiment contract, current research state, selected parent, relevant metrics/history/logs, remaining budgets, and implementation instructions.
Copilot operates inside the selected experiment code state and may modify only permitted paths.
When the session completes, control returns to the controller.
Research continuity comes from persistent state, not a permanently growing chat.

## 15. Copilot Output and Events

Copilot should leave code changes plus a structured decision containing parent experiment, observation, diagnosis, hypothesis, planned intervention, expected effect, and files changed.
This is a concise research decision record, not private chain-of-thought.
Useful events include:

```text
session_started · file_read · file_modified · command_started
command_completed · candidate_ready · session_completed · session_failed
```

Forward relevant events to the browser through WebSocket or SSE.
Raw reasoning streams are optional.

## 16. Experiment Contract and Project Loader

The contract is the formal boundary between arbitrary experiment code and AI Research Intern.
It defines objective, direction, target, execution configuration, required outputs, editable paths, protected paths, constraints, and budgets.
Autonomous research must not start until the contract is valid.
The project loader locates the contract, Azure job definition, Git repository, baseline results, output paths, and configured editable/protected surfaces.
The offline core loader now lives in `contracts/`; it validates versioned YAML,
literal scope paths, output layout, constraints, and declared budget limits.
SQLite binds the effective contract and dedicated repository before baseline import.
Reopening cannot silently change either binding. Azure job YAML is checked locally
as configuration data; explicit live service verification checks approved Azure
resources, and durable journals reserve compute/turn/credit allowances.
`workspace/discovery.py` imports ordinary folders/ZIPs into a source copy and a
separate asset store. `onboarding.py` persists reviewed project choices, selected
Azure resources and existing-run observations. `execution/discovery.py` owns
read-only Azure listing and download through the signed-in backend credential.
`workspace/setup.py` generates the internal contract, frozen scoring adapter and
service settings. Unknown evaluator arguments and ambiguous Azure resources are
resolved through the UI, not guessed by the controller.
`workspace/autoscoring.py` statically reads argparse declarations, local imports,
reference paths and YAML inputs. It generates an adapter for unambiguous scoring
interfaces and protects the scorer's transitive helpers/configuration. Retained
evaluation assets are hash-checked before and after scoring; the adapter copies
them into its temporary frozen-source tree and supplies isolated package paths.
`evaluation/runtime.py` prepares a separate compatible Python and declared binary
dependencies only during authorized preparation, with a reusable environment
receipt and setup log. Upload/inspection performs no imports or installation.
The environment installer is a pinned standalone uv executable extracted from
the compatible official PyPI wheel after SHA-256 verification. Bootstrap uses
standard-library HTTPS with the configured CA bundle and does not depend on
host pip, ensurepip or a system package manager. Publication is atomic and a
verified receipt permits offline reuse.
Downloaded runs are checked for predictions/checkpoints before environment setup;
missing named Azure outputs are retried explicitly. Training metrics are never a
fallback for independent evaluation.

The native command adapter preserves the selected YAML's environment, inputs and
command, stages hashed assets at their original paths, and adds identity/output
collection around the workload. A protected scorer recomputes objective metrics.
The existing prepared-workload adapter remains available. Single-node GPU command
jobs are supported; distributed jobs and pipelines are rejected before submission.

An existing baseline can be adopted from downloaded artifacts without another
Azure submission. Its receipt records the selected workspace/job, artifact hashes,
and explicitly user-attested source association. This attestation is distinct from
the exact Git snapshot enforced for all new submissions. The same frozen evaluator
must score the historical artifacts before the baseline can seed research.

## 17. Git / Workspace Manager

Git stores exact historical code states.
For the serial MVP, use one reusable experiment working directory and one Git commit per executed candidate.

```text
selected parent commit → reset workspace → Copilot edits
→ validate diff → commit EXP-N → execute that commit
```

Rejected experiments remain in Git and in the ledger.
The next candidate starts from the selected parent, not necessarily the latest commit.
Do not create a permanent worktree or full repository copy per experiment.

## 18. Git Safety

Before editing, verify the expected repository, parent commit, and acceptable working-tree state.
After editing, inspect changed files, reject protected-file changes, generate the diff, run preflight checks, and commit only a valid candidate.
Do not silently destroy unrelated researcher work.
For the hackathon, use a dedicated experiment repository/workspace.
This keeps resets, branching, and recovery predictable.

`workspace/git.py` implements this boundary for prepared offline repositories
under a run's `.runtime/` directory. It requires a clean, recorded HEAD before
detached parent checkout, compares complete filesystem inventories (including
ignored files) and Git metadata across the proposer turn, and stages exact
validated files. Unsupported Git configuration, links, submodules, attributes,
and unversionable empty directories fail closed. Candidate refs retain rejected
or interrupted commits. These checks are not an OS sandbox or arbitrary-repository onboarding.

Local source preparation now also uses this boundary. `workspace/importing.py`
checks a source-only upload, performs syntax/config checks without execution, and
stages a new independent Git repository with isolated config, templates and hooks.
It checks that every supplied file is versioned, without force-adding ignored files.
Only the staged `.git` directory is published into the reusable source workspace;
researcher file contents are preserved. `workspace.json` records its initial commit,
contract, source inventory and preflight evidence. This is setup, not `EXP-000`.
Preparation is idempotent; changed source, pre-existing Git metadata and incomplete
publication fail closed without resetting or deleting researcher work. Interrupted
publication requires inspection; automatic setup recovery is not claimed.

## 19. Validation Gate

The validation layer protects compute and scientific integrity.
Before Azure submission, check:

```text
contract validity · protected paths unchanged · candidate schema
syntax/configuration · budget availability · optional smoke test
```

Invalid candidates should fail before consuming GPU resources.
Do not build a heavyweight sandbox for the MVP.
Validation results should be visible to the controller and UI.

The offline preflight checks changed Python syntax and JSON/YAML configuration
without executing researcher code. Contract policy, the Azure job configuration,
and the output root are automatically protected. Shell smoke commands remain
unsupported. `controller/candidate.py` rechecks history/stop state and files before
committing and reserving a candidate. `workspace/lock.py` holds an OS advisory lock
across candidate preparation or a complete offline loop drive. It releases on
process exit; status reads and persistent human-stop requests remain available.

## 20. Azure Executor

The Azure executor is deterministic infrastructure around Azure ML.
Expose:

```text
submit_job(...) · get_status(...) · download_outputs(...) · cancel_job(...)
```

It submits the candidate, records the returned Azure job ID, polls terminal status, surfaces failures, and downloads outputs.
Copilot never receives raw Azure credentials.
The executor should work independently of Copilot.
Simple polling is sufficient for the MVP.

An opt-in implementation now exists in `execution/azure.py`, with a fake-service
test seam and lazy Azure SDK imports. It supports reviewed workload input bindings,
three controller-owned identity inputs and one named output, stages clean exact-commit source,
requires versioned assets and a digest-pinned image, and restricts operations to
durably recorded job identities. `ledger/services.py` stores immutable allowances
and submission intents in the run's SQLite database before service operations.
Ambiguous responses are reconciled by the saved name and intent tags, never by
automatic resubmission. Output publication is atomic and non-overwriting.

`copilot/live.py` provides fresh, restricted SDK sessions using `copilot/files.py`:
reviewed text reads, hash-checked writes to existing allowed files, and a structured
plan. It denies other permissions, configuration discovery and reasoning summaries.
Turn reservations precede startup; interrupted attempts are not replayed. The full
candidate diff still requires deterministic verification outside the adapter.

`live.py` composes these adapters into the shared serial controller. Ledger mode
is fixed at creation; old simulated databases remain readable and cannot be promoted.
A live baseline is submitted from the reviewed original commit and independently
scored before candidates may start. `evaluation/trusted.py` freezes protected scorer
files, runs a separate reviewed Python process without candidate imports, and
records identity/metric/source/artifact hashes. Self-reported training metrics are
validated as output data but never decide live success.

Reservations count verified GPU-seconds, coding turns and a shared AI-credit pool.
The SDK receives the whole remaining pool as its session limit. Final provider
`totalNanoAiu` settles credits (1e9 nano units per AI credit); unspent allowance
returns to the pool. Proven pre-prompt failures settle to zero; ambiguous outcomes
retain their reservation. Immutable settlement evidence and human credit additions
live beside the original intents in SQLite. Additions never rewrite the experiment
contract and never clear a human stop. Remaining allowance below the provider's
30-credit session minimum pauses research; approximate top-up guidance uses observed
usage, with no model call. Billing is distinct and provider limits remain soft.
`controller/readiness.py` reports setup gates, including measured baseline
acceptance and fresh read-only Azure/Copilot verification.

## 21. Azure and Output Lifecycle

```text
validated candidate → submit Azure job → persist job ID
→ mark running → poll → terminal state → download outputs
→ validate output contract
```

Copilot is inactive while Azure performs long-running compute.
Expected outputs are `run.json`, `metrics.json`, `metrics_history.json`, `logs/`, and `artifacts/`.
Large artifacts remain on disk; structured metadata enters the ledger.
The collector validates required outputs but does not judge experiment quality.

## 22. Evaluator

The evaluator is deterministic and independent of Copilot.
Inputs include the contract, parent result, candidate result, objective direction, target, and constraints.
Outputs include score, improvement over parent, new-best status, constraint status, goal status, and final decision.
Possible decisions are `KEEP`, `REJECT`, `FAILED`, and `GOAL_REACHED`.
The evaluator is the authority on experiment success.
A run can miss the final target while still becoming the new best.

## 23. Experiment Ledger

The ledger is the permanent scientific memory.
Use SQLite for structured metadata and filesystem storage for larger artifacts.
SQLite may store experiments, metrics, hypotheses, relationships, agent actions, and budget usage.
Filesystem storage may hold diffs, logs, plots, result JSON, and downloaded artifacts.
Do not introduce a vector database for the MVP.
An experiment is a scientific record, not merely a Git commit.

## 24. Experiment Record

Each experiment must be traceable to:

```text
experiment ID · parent experiment · parent commit · candidate commit
hypothesis · rationale · code diff · Azure job ID · execution status
metrics · constraint checks · decision · conclusion
```

Git reconstructs exact code state.
The ledger reconstructs scientific meaning and lineage.
Failed and rejected experiments remain part of the record.

## 25. Research State Builder

The ledger is permanent history; `research_state` is compact working memory.
After each evaluated experiment, derive objective, baseline, current best, last experiment, recent experiments, supported directions, rejected directions, open questions, and remaining budgets.
This state becomes the main research-memory input to the next fresh Copilot session.
It should always be reproducible from the ledger.
Do not maintain duplicate independent truth.

The offline implementation reads a consistent `Ledger.snapshot()` and builds
compact state in `ledger/research_state.py`. Shared values and continuation
rules live in `domain/research.py`; `controller/handoff.py` selects the best
eligible parent and invokes a fresh proposer through `copilot/proposer.py`.
The plan-only handoff proposer is scripted and cannot execute or edit code. A
separate scripted editor exercises the controlled candidate path. Exported
state/context JSON is for inspection; SQLite history and metadata remain
authoritative. The controller rejects a returned plan if that source state changed.

## 26. Persistence Model

Use exactly three persistence mechanisms:

```text
Git        → code history
SQLite     → structured research metadata
Filesystem → outputs, logs, diffs, artifacts
```

Each has one clear responsibility.
Do not add Redis, queues, distributed databases, or object storage unless a concrete blocker appears.
Together these stores must reconstruct any completed experiment.

## 27. FastAPI and Browser UI

FastAPI is a thin local transport layer; route handlers call application services rather than contain research logic.
The implementation uses `api/app.py`, `controller/mission.py`, and plain
HTML/CSS/JavaScript assets packaged under `api/static/`. `serve` binds to loopback;
FastAPI and Uvicorn are optional web dependencies. Routes expose run creation,
start/resume, persistent stop, rebuilt state, experiment details, and server-sent
snapshots containing recent persisted ledger events. No LLM reasoning stream is used.

One background thread runs the shared serial loop with its own SQLite
connection. Request reads use independent connections. An OS lock allows one web
server for the application workspace, while the existing per-run lock prevents a
second CLI/web driver from mutating that run. No queue or distributed worker is added.
The CLI acquires the web lock before starting Uvicorn and retains it through shutdown;
duplicate launches exit with an actionable message. Saved server address metadata is
only a diagnostic hint and never replaces OS lock ownership.
The browser distinguishes actual web-driver activity from persisted controller state.

The browser presents one research workspace with folder upload, compute/experiment/
credit limits, current activity and experiment evidence in a dialog. Simulation setup
controls remain in the developer CLI/API, not the main UI. Existing synthetic history
is labelled explicitly and is not attached to an uploaded project.

The prepared research source has one fixed handoff location:
`.runtime/research-project/repository/`. `api/uploads.py` bounds multipart transport.
The **Choose another project** action stages and validates a replacement upload,
then preserves the complete former project under `.runtime/project-history/`.
`workspace/switching.py` journals publication and resumes an interrupted switch at
server startup. The project lock inode remains in the active slot, and the serial
driver, pending ledger operations and existing-job watcher are excluded during
switching. Account sessions survive; project choices and model locks do not.
Requests bind to the current project identity so an old browser tab cannot replace
a newly loaded project. There is still only one active research loop.

`workspace/project.py` validates portable paths, stages files, checks the experiment
contract when present and publishes into an empty project slot. A source upload
without a contract remains available for review; onboarding generates its contract
from the reviewed fields before preparation. Upload never
executes source or replaces an existing project. Explicit local preparation creates
the independent Git workspace. Before live configuration or ledger initialization,
onboarding may revise a clean preparation using the researcher's reviewed choices.
The prior source, Git history and receipts are archived in `preparation-history/`;
a durable publication journal allows an interrupted replacement to resume. Unknown
edits block replacement. Live configuration and initialized research remain immutable.
Evaluation confirmation binds the reviewed protocol
to that exact source commit and contract digest in application-owned `evaluation.json`. `controller/mission.py` serializes project changes and
the web driver. The application-owned `settings.json` beside the repository stores
draft budgets bound to the contract digest, preserving the researcher's original files.

The optional evaluation protocol pins the evaluator entrypoint and validation split
by SHA-256 and records the metric definition, dataset version and procedure. These
paths are automatically protected. Result collection checks the protocol fingerprint
before passing structured metrics to the existing deterministic evaluator. This is
consistency checking, not proof that the workload actually ran the trusted evaluator.

Browser and CLI support live settings, read-only service checks, baseline measurement,
start/resume/stop and JSON reports with complete scored lineage. Live settings are
fixed for the run; draft budget settings must match the approved contract. Human
credit additions are audited runtime allowances layered over the original budget. Resource
checks expire after 24 hours. Start requires a measured accepted baseline. The live
phase is persisted, so resuming a baseline cannot silently start coding iterations.
Each offline run retains its independent synthetic fixture. Real cloud/model
acceptance remains an explicit configured run, not evidence supplied by offline tests.
The independent ML project remains usable without the platform.
There is no arbitrary filesystem-path API. Host/origin checks and same-origin mutation
headers protect the local boundary; this is not a remote authenticated service.
`connections.py` owns one cancellable provider-connection worker, serialized with
project changes and research. The dashboard exposes Azure/Copilot sign-in, provider
authorization links, safe connection status, cancellation and coding-model selection.
The API never returns tokens or provider subprocess logs. Reads do not contact providers.
An account change invalidates the previous service verification receipt.

Azure browser sign-in uses MSAL device authorization and an in-process cache. The
Azure execution adapter consumes that credential; CLI credentials remain a developer
fallback. Silent refresh cannot open a browser during a research job. The default
Azure SDK development client is for local testing; operators can configure their own
registered client and tenant with RESEARCH_INTERN_AZURE_CLIENT_ID and
RESEARCH_INTERN_AZURE_TENANT_ID.

The explicit Copilot sign-in action provisions checksum-verified, SDK-matched helpers
when needed. Windows/macOS use the provider's native credential manager and refuse
plaintext fallback. Linux/WSL uses an owner-only directory on verified tmpfs shared
memory for the app session, so no desktop keychain installation is required. The CLI
and restricted SDK share that session directory. Normal shutdown removes it; abrupt
process termination may leave the private volatile directory until host reboot.
Restarted apps require another session sign-in. Existing legacy CLI caches are not
migrated or deleted. These authentication changes never approve scientific policy,
mutate a live model choice, create a model turn or submit an Azure job.
The browser acts as research mission control.
It shows objective, baseline, best experiment, budget usage, current activity, hypothesis, Azure status, history, code diff, metrics, decision, and conclusion.
VS Code remains the primary coding environment.

## 28. Budgets, Permissions, and Credentials

Budget enforcement is deterministic.
Track experiment count and Azure compute usage; track Copilot/AI usage when practical.
The backend owns Azure credentials and never places secrets inside Copilot prompts, ledger entries, or research-state files.
Protected paths are checked after actual filesystem changes; prompt instructions alone are not a security boundary.
The controller owns continuation, stopping, and budget authority.
The LLM cannot extend its own permissions or budget.

The offline slice persists an immutable `max_experiments` before baseline import.
Candidate reservation atomically enforces continuation and consumes one slot,
including subsequent submission/runtime failures; baseline and draft proposals
do not consume slots. A persisted human stop blocks new proposals/reservations
and unsubmitted candidates while permitting collection of already submitted jobs.
Compute and Copilot credit accounting remain deferred and are unknown in state.

The offline loop also fixes an application-level `max_attempts` before baseline
import. Every preparation consumes an attempt before invoking the editor, including
preflight/proposer failures. This prevents unbounded retries without charging an
unexecuted candidate an experiment slot. Resume cannot reset either limit.

## 29. Failure, Recovery, and Serial Execution

Distinguish Copilot failure, candidate validation failure, Azure submission failure, Azure runtime failure, output-contract failure, evaluation failure, and application failure.
Infrastructure failure is not model-quality regression.
Persist current experiment ID, selected parent, candidate commit, Azure job ID, experiment status, ledger history, and budget usage.
On restart, reconcile local state with Azure status before launching another candidate.
The MVP is strictly serial: one Copilot iteration and one Azure experiment at a time.
Parallel search is post-MVP.

The offline implementation composes `CandidateController` and `ExecutionController`
in `controller/loop.py`. `ledger/preparations.py` journals parent snapshots, validated
code, commit checkpoints, failures, and the eventual experiment ID. Reservation and
journal linkage share one SQLite transaction. `workspace/recovery.py` completes
recorded Git operations or restores failed edits after archiving them. Restoration
requires exact recorded filesystem states and unchanged Git metadata; newer work
and unknown dirty edits are retained for manual reconciliation.

`offline.py` provides setup and composition for CLI `run`, `resume`, `status`, and
`stop`. Resume reconciles preparation and pending experiments before generating a
new candidate. A simulated `SUBMITTING` job is found by deterministic ID and checked
against its recorded candidate. Absence records submission failure without retrying
the submission. This is local simulator recovery, not an Azure recovery guarantee.
The CLI loop remains foreground and serial. The optional web server owns one
background driver thread. Graceful shutdown cancels the loop and records an
interruption without setting human stop. Startup lists existing runs but does not
auto-resume; explicit resume reconciles persisted state using the same recovery
rules. Legacy demos and incomplete baseline setup require separate handling.

## 30. Testing and Configuration

Prioritize deterministic components: contract validation, permissions, Git bookkeeping, evaluation, budgets, stopping logic, and research-state construction.
Mock Copilot and Azure adapters for controller tests.
Add one vertical integration test:

```text
baseline → mock candidate → mock Azure result
→ evaluate → ledger → new research state
```

Keep application configuration separate from experiment contract configuration.
Operational logs are diagnostics; the ledger is the scientific record.

## 31. Architectural Non-Goals

Do not introduce microservices, message brokers, distributed workers, Kubernetes, vector databases, complex agent hierarchies, multi-tenant authentication, parallel search infrastructure, or generic cloud abstractions.
Do not create dedicated platform layers for PyTorch, TensorFlow, JAX, YOLO, or Transformers.
Prefer direct Python composition and narrow typed interfaces.
Only add complexity when the working vertical loop exposes a concrete need.

## 32. Core Interfaces

```text
CopilotAdapter.run_iteration(...)
GitManager.prepare_parent(...)
GitManager.commit_candidate(...)
PreflightValidator.validate(...)
AzureExecutor.submit_job(...)
AzureExecutor.get_status(...)
OutputCollector.collect(...)
Evaluator.evaluate(...)
Ledger.record_experiment(...)
StateBuilder.build(...)
```

The Research Controller composes these interfaces.
The interfaces matter more than exact filenames.

## 33. Implementation Sequence

While live-service validation is deferred, the execution/evaluation/ledger boundary
has an offline proof under the same module ownership. A simulated executor emits
standard outputs; the real controller, evaluator, SQLite ledger, and filesystem
record one prepared candidate. Two local fixture Git commits provide code anchors.
Simulation ledgers are explicitly labelled and isolated, and this controller
currently refuses live adapters. The original demos use an evaluation-rules subset;
the new `candidate-demo` persists the full core contract, prepares actual parent
code, validates mock edits, commits candidates, and feeds the same execution pipeline.
Research-state handoff and experiment-count
stopping now extend that proof through fresh scripted proposals after an improvement
and a rejection/failure. Candidate attempts preserve context, plan, validation,
diffs and failures under `candidate_attempts/`; successful commit evidence is
written under `candidates/` before reservation and linked to the experiment.
The bounded offline loop now repeats this path with fresh mock editors and persisted
attempt limits. Invalid edits spend no experiment slot; matching failed edits can be
archived and restored automatically. Recorded commits and pending simulated jobs
resume without duplicate IDs or submissions. Unknown work and Git tampering require
inspection. This proof does not replace live Copilot editing or Azure validation.

```text
1. prove Copilot SDK session on a controlled repo
2. add contract + Git candidate lifecycle
3. execute one candidate on Azure
4. collect + evaluate + persist EXP-001
5. build research-state handoff
6. automate repeated iterations
7. add minimal browser UI + events
```

Build vertically.
Do not fully implement every module before one complete experiment works end to end.

## 34. Architectural Invariants

1. Copilot never decides final success.
2. Copilot never owns Azure credentials.
3. Copilot never owns permanent research state.
4. Protected files are checked deterministically.
5. Every executed experiment maps to an exact Git commit.
6. Every evaluated experiment maps to a ledger record.
7. The ledger is the source of truth for research history.
8. Research state is derived from the ledger.
9. Only one experiment runs at a time.
10. The controller owns continuation and stopping.
11. Azure ML is an execution backend, not an agent.
12. The browser observes and controls; it does not contain research logic.

## 35. Final Architecture

```text
Human Researcher
↓
Browser UI / VS Code
↓
FastAPI Backend
↓
Research Controller
├─ Contract Layer
├─ Copilot Adapter
└─ Git / Workspace Manager
↓
Candidate → Validation → Git Commit
↓
Azure ML Executor → Azure ML
↓
Output Collector → Evaluator
↓
Experiment Ledger → Research State Builder
└──── next Copilot iteration
```

This architecture should remain small, explicit, and deterministic around the LLM.
