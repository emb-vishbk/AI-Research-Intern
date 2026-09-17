# AI Research Intern

## README.md

## 1. Overview

AI Research Intern is an autonomous optimization system for computational ML experiments.
A human researcher provides a working experiment, objective, evaluator, Azure ML execution, and baseline.
The system then inspects evidence, proposes a candidate, modifies code, executes it, evaluates the result, records what happened, and repeats.
The MVP targets constrained, machine-evaluable optimization rather than open-ended autonomous science.
The evaluator, not the LLM, decides whether an experiment improved.

```text
inspect → propose → modify → validate → execute
→ evaluate → record → update state → repeat
```

### Live integration status and human setup gates (2026-09-17)

**The end-to-end live MVP is not complete.** The serial application still runs
simulations only. New opt-in adapters in `copilot/live.py` and `execution/azure.py`
are tested against fake services, but are not yet connected to the research
controller, baseline import, or live UI execution. Start remains blocked. The
dashboard now exposes individual readiness checks rather than one generic message.
The read-only `research-intern readiness` command reports the same local gates;
`--json` provides structured output. Exit status 1 means live execution is blocked,
not that a cloud operation failed. It performs no authentication or service calls.

The coding adapter uses a fresh session, a reviewed source-read allowlist,
existing-file replacements checked against the contract and current file hash,
and a structured final candidate plan. Shell, Git, filesystem discovery and
arbitrary tools are denied. Azure credentials are excluded from its environment.
The controller must still verify the complete diff; these capabilities are not an
OS/network sandbox. Source files sent to Copilot must be reviewed to exclude secrets
and held-out data. Admitted coding attempts cannot be replayed after interruption.

The Azure adapter supports the prepared workload's command-job interface: config,
dataset, weights, experiment ID, parent ID and exact source commit, with one named
`experiment_outputs` output. It stages the clean committed source separately from
Git metadata, pins versioned workspace assets and an image digest, persists an
intent and deterministic job name before submission, and reconciles ambiguous
responses without submitting again. Only its recorded jobs may be polled/cancelled.
Cancellation is a request; polling confirms completion. Partial downloads are
never published as collected outputs, and existing output bundles are not replaced.

`ledger/services.py` persists immutable service allowances and reservations in the
run's `ledger.sqlite3`. GPU reservations use declared GPU count × job timeout;
coding reservations count turns, **not AI credits**. Failures and ambiguous calls
retain reservations. Actual usage is unknown; these are not hard billing caps.
Azure provisioning, idle time, cancellation delays and provider billing require
separate controls. The optional `azure` dependency group is required for real SDK
access, not for ordinary fake-service tests. No SDK runtime download or interactive
login is performed by these adapters.

Before any live use, the researcher must:

1. **Approve science and limits.** Review the uploaded project's
    `RESEARCH_OBJECTIVE.md`, `HANDOFF_STATUS.md`, protected policy and split manifest.
    Confirm metric, split, evaluator, editable scope, initialization/checkpoint rules,
    training-time and GPU-memory ceilings, experiment count, total GPU allowance,
    job timeout, coding-turn allowance and AI spending limit. Do not copy historical
    measurements into these limits. Reconcile policy and contract before freezing
    source or confirming evaluation.
2. **Authenticate Copilot locally.** Follow the isolated-runtime setup later in
    this README using the already provisioned runtime and an entitled account.
    VS Code authentication alone does not prove the isolated runtime is signed in.
    Keep tokens in local authentication/environment mechanisms, never source,
    contracts, prompts or chat. Verify account/provider spending controls before
    authorizing even the read-only live spike. Model credit cost is not inferred
    from token or tool counts.
3. **Verify Azure in Azure ML Studio.** Select the intended subscription, resource
    group and workspace. Check permission to submit jobs to the approved compute;
    record its GPU SKU/count. With the resource owner's approval, use serial compute
    and appropriate idle shutdown; do not alter a shared cluster blindly. Inspect an
    explicitly versioned environment using an image pinned by SHA-256, with no
    mutable build/conda overlay. Verify versioned, read-only train/validation data
    and initial-weights assets; do not expose final held-out test data. Then sign in
    to the Azure CLI yourself using the organization's approved login flow. The
    backend uses `AzureCliCredential`, not automatic interactive sign-in. Share only
    non-secret resource names/versions/digests when configuring the application.
4. **Establish valid baseline and scoring evidence.** The saved historical Penn-Fudan
    job is explicitly *not* an accepted `EXP-000`; its source commit was not recorded
    and the current source differs. Preserve it unchanged. Prefer a fresh measured
    baseline after policy/source finalization, unless exact historical provenance
    can be independently recovered. Update and verify the workload's missing output
    evaluation fingerprint before freezing source. Tags, declared commits, matching
    hashes and self-reported metrics alone do not prove trusted evaluation: scoring
    must be verified independently of editable training code.

Remaining application work includes live ledger mode/migration, measured baseline
acceptance, independently trusted scoring, controller budget admission/recovery,
SDK/service integration verification, live UI composition, and the real multi-trial
demonstration. Sign-in alone will not enable Start or complete these steps.

## 2. Core Thesis

The project combines LLM reasoning with external evaluation and persistent experiment memory.
Copilot is used for code understanding, diagnosis, hypothesis generation, and controlled code modification.
Deterministic software owns permissions, budgets, execution, scoring, persistence, recovery, and stopping.
Azure ML is the remote computational laboratory.
The research loop is the product; broad integrations are secondary.

## 3. MVP Goal

The hackathon MVP must show that an AI coding agent can autonomously improve an existing ML experiment through several traceable iterations.
A convincing demo includes a baseline, autonomous experiments, at least one rejected or failed direction, measurable improvement, exact code lineage, Azure execution, and persistent memory.
The system should require no human intervention between normal iterations.
A small reliable vertical loop is more important than production breadth.

## 4. Supported Problem Model

The first domain is machine learning.
Suitable tasks include fine-tuning, distillation, quantization, regularization, augmentation, optimizer/scheduler changes, hyperparameter tuning, and architecture changes.
A valid problem is represented conceptually as:

```text
(S, f, C, B)
S = allowed intervention space
f = machine-executable objective
C = constraints
B = experiment/compute budget
```

The system searches for a better candidate inside the allowed space.

## 5. Human / System Boundary

The human defines the research problem and scientific boundaries.
The human provides:

```text
experiment codebase
objective + direction + optional target
evaluator
editable/protected scope
constraints + budgets
Azure ML execution
baseline
```

The Research Intern decides what evidence matters, what hypothesis to test, and what permitted candidate to try next.

## 6. Runtime Architecture

```text
Human Researcher
↓
Browser UI / VS Code
↓
Local FastAPI Backend
↓
Research Controller
├─ Contract Layer
├─ Copilot Adapter
├─ Git / Workspace Manager
├─ Validation Gate
├─ Azure ML Executor
├─ Evaluator
├─ Experiment Ledger
└─ Research State Builder
```

The MVP is a modular monolith.

## 7. Architectural Rule

Think of the product as:

> A deterministic experimentation platform with an LLM-powered research engine inside it.
> Copilot owns semantic reasoning and controlled code modification.
> Normal software owns Git state, permissions, budgets, Azure credentials, Azure lifecycle, scoring, persistence, recovery, and stopping.
> Do not move these authority boundaries into the LLM.

## 8. Copilot SDK

The MVP uses the Python `github-copilot-sdk`.
Copilot provides repository inspection, hypothesis generation, controlled editing, permitted commands, and event streaming.
Our application controls when each research iteration starts and ends.
Use a fresh Copilot session per experiment where practical.
Continuity comes from the ledger and research state, not one growing chat.

## 9. Repository Strategy

AI Research Intern is being built from a clean repository.
We are not cloning `github/awesome-copilot` as the application base.
Official Copilot SDK and Ralph-loop examples are references for client startup, sessions, working-directory control, events, fresh iterations, and shutdown.
Borrow mechanisms, not repository structure.

## 10. Repository Layout

```text
ai-research-intern/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── docs/
│   ├── PROJECT.md
│   ├── MVP_SCOPE.md
│   ├── ARCHITECTURE.md
│   ├── RESEARCH_LOOP.md
│   └── EXPERIMENT_CONTRACT.md
├── schemas/
├── src/research_intern/
├── tests/
└── ui/
```

Exact internal filenames may evolve while documented module boundaries remain stable.

## 11. Documentation Map

`RESEARCH_INTERN_REQUIREMENTS.md` is the portable guide to copy into an independent
ML repository and give its coding agent alongside the research task. It describes
standalone operation, the supported contract/output formats, trusted evaluation,
baseline evidence, and planned execution bindings. It is derived from the existing
contract and validators; it does not imply external-project loading or live service
integration is already implemented.

`docs/PROJECT.md` defines the thesis and product concept.
`docs/MVP_SCOPE.md` defines the hackathon boundary.
`docs/ARCHITECTURE.md` defines technical ownership, persistence, and dependencies.
`docs/RESEARCH_LOOP.md` defines the experiment-to-experiment lifecycle.
`docs/EXPERIMENT_CONTRACT.md` defines the researcher-experiment interface.
`AGENTS.md` defines operating rules for coding agents.

## 12. Researcher Experiment

The experiment is logically separate from the Research Intern application.
A representative project may look like:

```text
experiment/
├── train.py
├── model.py
├── dataset.py
├── evaluate.py
├── configs/
├── azure_job.yaml
└── .research_intern/
    └── contract.yaml
```

The experiment should remain ordinary researcher-written ML code.

## 13. Experiment Contract

The contract declares objective, direction, target, execution configuration, outputs, editable paths, protected paths, constraints, and budgets.
Autonomous research must not begin until the contract is valid.
The contract standardizes the boundary without standardizing the whole codebase.
See `docs/EXPERIMENT_CONTRACT.md`.

## 14. Standard Outputs

Each executed experiment should produce:

```text
experiment_outputs/
├── run.json
├── metrics.json
├── metrics_history.json
├── logs/
└── artifacts/
```

Structured metrics are authoritative.
Do not infer objective values from arbitrary logs or plots.

## 15. Experiment Identity

The human baseline is `EXP-000`.
Autonomous experiments are `EXP-001`, `EXP-002`, `EXP-003`, and so on.
Bootstrap work before the baseline is not an experiment.
Every executed experiment maps to an exact Git commit and a ledger record.

## 16. Research Loop

```text
load state → select parent → reset workspace
→ fresh Copilot session → observe/diagnose/hypothesize
→ implement candidate → inspect diff → preflight
→ commit → Azure ML → collect outputs
→ evaluate → record → rebuild state → repeat/stop
```

See `docs/RESEARCH_LOOP.md` for detailed branches and stopping behavior.

## 17. Git and Workspace

Git stores exact historical code states.
The ledger stores scientific meaning.

```text
Git → exact code state
Ledger → parent + hypothesis + rationale
       → Azure run + metrics + decision + conclusion
```

The serial MVP uses one reusable experiment working directory.
A permanent worktree per experiment is unnecessary.

## 18. Experiment Outcomes

The deterministic evaluator may return:

```text
KEEP
REJECT
FAILED
GOAL_REACHED
```

`KEEP` means the candidate remains eligible as a future parent.
`REJECT` means the run completed but should not become the active best.
`FAILED` means meaningful evaluation could not be completed.
`GOAL_REACHED` means target and hard constraints are satisfied.

## 19. Azure ML

Azure ML is the remote experiment laboratory.
The Azure adapter should expose:

```text
submit_job(...)
get_status(...)
download_outputs(...)
cancel_job(...)
```

Persist the Azure job ID immediately after submission.
Copilot should be inactive while long-running Azure compute executes.

## 20. Persistence and Research State

The MVP uses:

```text
Git        → code history
SQLite     → structured research metadata
Filesystem → outputs, logs, diffs, artifacts
```

The ledger is permanent memory.
A compact research state is derived from it for each new Copilot session.
That state should summarize objective, baseline, best experiment, recent outcomes, supported/rejected directions, failures, open questions, and remaining budget.

## 21. Protected Surfaces

The runtime agent must not change the definition of success.
Typical protected surfaces are the evaluator, held-out/test data, objective, constraints, budgets, effective contract, and researcher-declared protected paths.
Prompt instructions are not a security boundary.
The controller verifies the actual filesystem/Git diff before execution.

## 22. Budgets and Stopping

The controller may enforce maximum experiment count, Azure GPU-hours, Copilot/AI usage, and human stop.
Optional future rules may include wall-clock or stagnation limits.
The LLM cannot change its own limits.
No new Copilot session should begin after a hard stopping condition is reached.

## 23. Browser UI

The browser acts as research mission control.
It should eventually show objective, baseline, best result, budget usage, current experiment, hypothesis, agent activity, Azure status, experiment history, code diff, metrics, decision, and conclusion.
VS Code remains the primary development environment.
The UI contains no core scientific decision logic.

## 24. Current Project State

The project has a controlled Copilot spike, offline execution/evaluation/ledger,
research-state handoff, an offline contract + controlled candidate workflow, and a
bounded offline research loop with basic restart recovery.
Available:

```text
clean project folder created
Python virtual environment created
github-copilot-sdk installed
core project documentation defined
installable Python package + explicit Copilot spike command
fixed read-only README tool + controlled fixture
offline tests for permissions, session failures, and cleanup
simulated job submission, polling, and standardized output collection
deterministic evaluation + SQLite ledger + filesystem evidence
one-candidate offline demo with separate fixture Git commits
research state reconstructed from ledger history + fresh scripted proposal handoff
persisted experiment limits, goal checks, and human stop requests
strict YAML contracts + immutable contract/repository binding
selected-parent checkout + actual filesystem permission checks + preflight
validated candidate commits connected to simulated execution and research memory
bounded serial offline loop + preparation journal + run/status/resume/stop CLI
basic recovery for failed edits, interrupted commits, reservations and simulated jobs
localhost FastAPI + browser mission control, live snapshots, background offline driver
fixed local research repository location with read-only contract readiness checks
```

The spike lives in `src/research_intern/copilot/spike.py`; the CLI lives in
`src/research_intern/main.py`. The offline slice uses `controller/`, `execution/`,
`evaluation/`, `domain/`, and `ledger/`. The candidate workflow adds `contracts/`,
`workspace/git.py`, `validation/`, and `controller/candidate.py`. The offline loop
adds `controller/loop.py`, `ledger/preparations.py`, and workspace recovery/locking.
The optional `api/` transport and its packaged browser assets call
`controller/mission.py`; the existing research loop retains scientific authority.
Live Copilot validation/editing, Azure integration, real baseline import, measured ML
experiments, and compute/AI budget enforcement remain to be completed. Synthetic demo outcomes
do not establish scientific improvement or validate live service access.

## 25. Local Development

Use the existing virtual environment.
On Windows PowerShell it will typically be activated with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Do not recreate the environment without a reason.
Keep project dependencies isolated inside it.

From this workspace in Windows PowerShell, install the application in editable mode:

```powershell
New-Item -ItemType Directory -Force .runtime/tmp | Out-Null
$env:TEMP = (Resolve-Path .runtime/tmp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -e .
.\.venv\Scripts\python.exe -m research_intern.main --help
```

The application pins the already installed `github-copilot-sdk==1.0.13` because
the spike uses its current keyword-based API. Installation may download the
packaging build dependency. Source changes take effect through the editable install.

## 26. Dependency Policy

Add dependencies only when the current milestone requires them.
The initial MVP does not require LangChain, LangGraph, Microsoft Agent Framework, Optuna, Celery, Redis, or vector databases.
The Copilot SDK already supplies the runtime coding-agent loop.
Introduce another framework only after a concrete need appears.

## 27. Development Plan

Build vertically:

```text
1. minimal Copilot SDK spike
2. controlled Git candidate modification
3. one complete Azure experiment
4. deterministic evaluation + EXP-001 ledger record
5. research-state handoff into EXP-002
6. autonomous repetition
7. minimal localhost UI
```

Do not fully build every subsystem before one end-to-end experiment works.

## 28. Running the First Milestone

The spike exercises:

```text
Python
→ start Copilot client
→ create session
→ set controlled working directory
→ send simple task
→ receive result/events
→ close cleanly
```

Provision the SDK's pinned runtime explicitly inside this workspace (network required):

```powershell
$env:COPILOT_CLI_EXTRACT_DIR = Join-Path (Get-Location) '.runtime/copilot-sdk'
.\.venv\Scripts\python.exe -m copilot download-runtime
```

Use the runtime executable path printed by that command. On Windows x64:

```powershell
.\.venv\Scripts\python.exe -m research_intern.main spike --live `
  --working-directory tests/fixtures/copilot_readonly `
  --runtime-path .runtime/copilot-sdk/prebuilds/win32-x64/copilot-runtime.exe
```

This is a real Copilot call and can consume AI usage. `--live` is mandatory.
`--model` optionally selects a model; otherwise the runtime chooses its default.
`--timeout` defaults to 60 seconds across startup, authentication, and the turn;
bounded cleanup can take additional time. The spike never downloads a runtime itself.

The isolated runtime uses SDK empty mode and workspace-local state. It does not
use the OS keychain; configure supported Copilot authentication in the local process
environment, such as `COPILOT_GITHUB_TOKEN`. A VS Code login alone does not establish
authentication for this isolated runtime. Never put credentials in source or chat.

The model receives only a zero-argument `read_experiment_readme` tool, whose handler
reads the selected directory's UTF-8 `README.md` (maximum 32 KiB). All other tools
are unavailable and other permission requests are denied. Ambient configuration,
skills, hooks, and Git operations are disabled. This is a narrow capability boundary,
not a general-purpose OS sandbox. All selected paths must resolve inside this workspace.

Expected output includes `[spike] readme_read`, SDK activity events, a final summary
about the apple/pear classifier with `ORCHARD-READ-7319`, and `[spike] client_stopped`.
The spike rejects a missing tool read, empty response, or changed README and exits
nonzero on failure. Inspect the summary and marker to verify the model used the fixture.
It creates no experiment IDs or Git commits. Runtime state and downloads stay under
ignored `.runtime/`; this spike does not use the separate offline experiment ledger.

After this live session succeeds, the next milestone is one permitted file change,
deterministic diff validation, and a candidate Git commit in a dedicated experiment repo.

### Offline execution → evaluation → ledger demo

This slice can be exercised before obtaining live-service permissions. It needs
Python 3.11+ and an existing Git executable, with no additional Python dependencies,
authentication, model calls, training, or Azure execution. From this workspace:

```powershell
.\.venv\Scripts\python.exe -m research_intern.main simulate
```

For the dependency-free slice using Linux/WSL Python directly:

```bash
PYTHONPATH=src python3 -B -m research_intern.main simulate
```

Each invocation creates a fresh, explicitly labelled simulation directory under
`.runtime/simulations/` and prints its path. The sequence is:

```text
seed two local fixture Git commits
→ import synthetic EXP-000 baseline (F1 0.80)
→ reserve EXP-001 and its candidate commit
→ submit simulated job and immediately persist its ID
→ poll simulated running/completed statuses
→ collect standardized synthetic outputs
→ evaluate F1, target 0.87, and latency <= 50 ms
→ record decision, metrics, constraints, lineage, and lifecycle events
```

The default candidate has F1 `0.84`, so it becomes `KEEP` and the current best.
Choose `--scenario regress`, `constraint`, `goal`, `runtime-failure`,
`invalid-output`, or `submission-failure` to exercise the other branches.
An intentionally failed experiment still exits the demo successfully if its
failure was recorded correctly; a failure of the demo itself exits nonzero.

The printed directory contains:

```text
simulation.json                 # identifies synthetic evidence and zero service usage
ledger.sqlite3                  # experiments, lifecycle events, fixed evaluation rules
fixture_repo/.git/              # exact baseline/candidate commits for this fixture
simulated_jobs/                 # resumable local simulated job state
experiments/EXP-000/experiment_outputs/
experiments/EXP-001/diff.patch
experiments/EXP-001/experiment_outputs/  # when outputs exist
```

Fixture Git commands use a local simulation identity and empty template/hooks,
without inheriting user/system Git configuration. These settings apply only to
the fixture subprocesses; they do not change machine Git settings. The application
repository is not staged, committed, reset, or pushed by the demo. Runtime and
test artifacts stay under `.runtime/`; directory checks reject traversal and
existing symlinks. This is an application path boundary, not an OS sandbox.

`ExecutionController` accepts a candidate that the earlier Git/validation stage
is expected to have validated. The demo prepares that candidate itself; it does
not implement Copilot editing or permission enforcement for arbitrary research
repositories. The controller currently refuses live execution adapters.

The same ledger can be reopened through the Python service to poll its saved job
without resubmitting, or finish recording already collected outputs. An interrupted
`SUBMITTING` state requires explicit job reconciliation and blocks further candidates.
Terminal records cannot be overwritten. The CLI always starts a new simulation;
a resume command, full restart recovery, and compute/AI budget accounting belong
to later slices. The one-candidate demo now persists a one-experiment limit.
IDs are unique within each run's ledger.

This older demo uses an immutable `EvaluationRules` snapshot (objective, target,
and min/max constraints). The candidate demo below adds full core contract loading,
scope enforcement, and custom output paths. Azure service validation remains pending. Standard output
shapes are documented in `schemas/run.schema.json` and `schemas/metrics.schema.json`;
the standard-library collector enforces these core shapes plus cross-file checks.
JSON evidence is limited to 4 MiB per file, and history contains numeric arrays.
The MVP collector requires both `logs/` and `artifacts/`, which may be empty.

### Research-state handoff demo

The next offline slice connects the ledger to a fresh scripted proposer. Run:

```powershell
.\.venv\Scripts\python.exe -m research_intern.main handoff-demo
```

Or use dependency-free Linux/WSL Python:

```bash
PYTHONPATH=src python3 -B -m research_intern.main handoff-demo
```

It records a synthetic improvement followed by a rejection, rebuilding state
after each result. Each permitted handoff creates a new scripted proposer and
passes the objective, constraints, selected parent commit, recent results,
negative evidence, and remaining experiment slots. The second fixture candidate
matches the first scripted plan. After the rejection, the next plan selects
`EXP-001` as parent while retaining `EXP-002` as the latest result. That final
plan is saved for inspection and is not executed.

Use `--outcome runtime-failure` for failure evidence, `--outcome goal` for target
stopping, or `--max-experiments 2` to stop after two candidates without requesting
another proposal. The default limit is three; zero permits baseline setup only.

Artifacts stay under the printed `.runtime/simulations/handoff-.../` directory:
the existing ledger, fixture commits, and experiment outputs, plus
`research_state.json` and `handoff-after-EXP-N.json` for each permitted handoff.
These JSON files are reviewable exports. Rebuilding state reads a consistent
SQLite snapshot, never a cached export. Summaries retain at most five recent
results and five findings per outcome category by default; long narrative fields
are truncated, while full evidence remains in the ledger/files. Questions are
deterministic prompts for investigation, and recorded outcomes are not claims of
causal proof. Raw logs and diffs are referenced, not loaded into the context.

The code connects through these boundaries:

```text
ledger/sqlite.py            persistent history, limits, stop flag, atomic reservation
ledger/research_state.py    deterministic compact working memory
domain/research.py          shared state, continuation rules, context, candidate plan
controller/handoff.py      stopping checks, parent selection, fresh proposal invocation
copilot/proposer.py         narrow async proposal interface; no SDK dependency
copilot/simulated.py        scripted development substitute; no model or file edits
workspace/fixture.py        shared disposable Git setup for both offline demos
```

Experiment-count policy: set `max_experiments` before importing the baseline.
Reserving a candidate consumes one slot, including failed submission/execution.
The baseline, draft plans, and candidates rejected before reservation consume no
slots. The limit is immutable and is checked atomically during reservation;
reopening the ledger cannot reset the count. The final reserved slot can still
be submitted and collected. Legacy ledgers without a configured limit remain
readable but cannot start new candidates; create a fresh simulation with a limit.

New proposals and reservations are blocked by a reached target, exhausted or
missing budget, a pending experiment, or a human stop. `Ledger.request_stop()`
persists a stop for the run and prevents submission of a prepared candidate;
already submitted jobs can still be collected and recorded. It does not request
remote cancellation. A returned proposal is discarded if history or stopping
state changed while it was being generated.

This demo still uses scripted choices and synthetic metrics. Copilot/Azure usage
remains zero; their remaining account/compute allowances are unknown (`null`) in
research state. The proposer cannot assign IDs or decide experimental success.
This plan-only handoff supplies the parent reference without restoring code.
The candidate workflow below prepares the actual parent before editing.
Live proposers remain disabled.

### Offline contract → controlled candidate demo

Using the existing Linux/WSL Python environment (Python 3.11+, Git, and PyYAML):

```bash
PYTHONPATH=src python3 -B -m research_intern.main candidate-demo
PYTHONPATH=src python3 -B -m research_intern.main candidate-demo --scenario protected
PYTHONPATH=src python3 -B -m research_intern.main candidate-demo --scenario allowed
```

The default demo loads a prepared `.research_intern/contract.yaml`, persists the
effective contract and repository identity before importing the synthetic baseline,
then makes two real local file edits with fresh mock proposers. Each valid edit
passes scope and syntax checks, becomes a Git commit, and enters the existing
simulated execution/evaluation/ledger pipeline. The first result is `KEEP`; the
second is `REJECT`; the working directory is restored to the first candidate's
commit. All scores and research hypotheses remain synthetic.

The `protected` scenario attempts to change `evaluate.py`. The controller blocks
it before reserving an experiment or submitting a job. Invalid edits and failure
diagnostics remain for inspection; a dirty workspace blocks subsequent preparation.
The `allowed` scenario runs one permitted labeller edit. No candidate code, shell
smoke command, model call, or Azure job executes in any of these demonstrations.

`CandidateController.prepare_candidate()` returns a persisted `PREPARED` record;
`ExecutionController.resume_submission()` and `advance()` handle the next stages.
Each run lives under `.runtime/simulations/candidate-*/` and contains:

```text
fixture_repo/                        dedicated experiment repository
ledger.sqlite3                       immutable contract, repository, budgets, history
candidate_attempts/<attempt>/        context, plan, validation, diff or failure details
candidates/<commit>.json             candidate evidence written before reservation
experiments/EXP-N/candidate.json      plan, changed paths, checks, exact commit
experiments/EXP-N/diff.patch          saved before submission
experiments/EXP-N/<output root>/      configured output files and directories
research_state.json                  derived, reviewable state export
restored_parent.json                 default demo's selected-parent proof
```

Workspace checks cover tracked, untracked, and ignored files, protected deletions
and renames, Git metadata changes, and links. They refuse unexpected dirty work or
unrecorded HEAD commits. Candidate commits have durable Git refs so rejected code
survives later parent checkouts. No platform Git history is changed.

This is deliberately limited to prepared repositories inside a run under this
workspace's `.runtime/`. It supports ordinary SHA-1 Git repositories with basic
core configuration, regular files, and no worktrees, submodules, attributes,
links, or empty unversioned directories. Fixture limits are 16 MiB per file,
128 MiB per scanned tree, and 10000 entries. These are application checks, not
an OS sandbox or a large-dataset onboarding implementation.

Git metadata belongs to the controller. The effective contract, Azure job
configuration, and generated output root are automatically protected in addition
to declared protected paths. Preflight compiles changed Python without executing
it and validates changed JSON objects/YAML. Shell smoke commands are unsupported.
The standalone candidate demo retains failed edits for inspection. Automatic
recovery is provided by the initialized offline loop described below.

`PyYAML>=6.0.1,<7` is declared for this slice; the verified Linux environment
already provides 6.0.1. Development used no downloads or authentication.

### Offline research loop and basic recovery

Run the complete serial loop with the existing Python 3.11+, Git, and PyYAML:

```bash
PYTHONPATH=src python3 -B -m research_intern.main run
```

The command creates and prints a directory under `.runtime/simulations/loop-*/`.
It imports a synthetic baseline, repeatedly prepares and commits candidates from
the current best, submits local simulated jobs, evaluates outputs, and rebuilds
research memory. Each preparation uses a fresh mock editor. The default sequence
exercises `KEEP`, `REJECT`, `FAILED`, and `GOAL_REACHED` across four experiments.
The fixture scores are canned; this proves orchestration, not measured ML improvement.
No model, training code, network service, or Azure compute is invoked.

To pause after preparing a candidate and then resume the same run:

```bash
PYTHONPATH=src python3 -B -m research_intern.main run --steps 1
# Replace RUN_DIRECTORY with the path printed above.
PYTHONPATH=src python3 -B -m research_intern.main status RUN_DIRECTORY --json
PYTHONPATH=src python3 -B -m research_intern.main resume RUN_DIRECTORY
PYTHONPATH=src python3 -B -m research_intern.main stop RUN_DIRECTORY
```

`run` and `resume` drive the loop in the foreground. `--steps N` pauses after N
controller transitions; Ctrl+C interrupts it. `status` reconstructs state from
the ledger, including best/latest, evidence references and budgets. Its controller
status is the last persisted observation, not a process-liveness guarantee.
`stop` persists a permanent human stop; it can be issued from another terminal.
An active driver checks it between operations. Already submitted jobs may finish
collection; prepared candidates are not submitted. Stopping does not cancel a job,
start a background worker, or clear on resume. Start a new run for a fresh budget.

`--max-experiments` defaults to 4. `--max-attempts` defaults to twice that value
and bounds all candidate preparations, including failures before reservation.
Both limits persist across restart. A preparation is counted before editing;
an experiment slot is counted only when the validated commit is reserved.
Try `--scenario invalid-first`, `invalid-always`, or `protected-first` to exercise
safe restoration and retries; `goal` and `runtime-failure` exercise early stopping
and repeated failed jobs. Zero for either limit prevents new candidate work.

SQLite journals preparation stages before long operations and links reservation
to its preparation in one transaction. Recovery finishes interrupted validated
commits, publishes missing evidence, submits an existing `PREPARED` candidate,
and polls/collects an existing job without allocating another ID. For an interrupted
`SUBMITTING` operation, the simulator checks its durable job file: a matching job
is attached; proven absence records submission failure without resubmission.
Real Azure reconciliation remains deferred.

Failed edits are archived under `candidate_attempts/<attempt>/rejected_files/`
before restoring their recorded parent. Recovery requires the filesystem to match
the recorded failure or an interrupted restoration. Newer edits, altered Git
metadata, unknown locks, and abruptly interrupted dirty edits without a final
inventory stop for inspection. Recovery does not run a blanket Git reset/clean.
An OS advisory lock serializes managed drivers and releases on process exit;
matching stale candidate markers are cleared only while that lock is held.
This recovery applies to initialized offline loop runs and prepared fixtures;
legacy demos and interrupted baseline setup are not automatically migrated.

### Localhost mission control

From a normal **Windows CMD** terminal:

```bat
cd /d "E:\AI Research Intern"
start-web.cmd
```

Open **http://127.0.0.1:8000**. Keep CMD open and press **Ctrl+C** there to
stop the server. If CMD asks `Terminate batch job (Y/N)?`, answer `Y`.
The launcher runs the existing WSL Python environment; it does not install
anything or require an interactive WSL terminal. It uses the default WSL
distribution, which must be the one containing the provisioned environment.
Use `start-web.cmd --port 8001` for a different port.
Only one web server can own this workspace, even on different ports. A second
launch reports the existing server and its last recorded address; use that server
or stop it with Ctrl+C in its original terminal before restarting. The OS releases
the lock when its process exits; do not delete `operation.lock` to bypass it.

Open browser event streams get three seconds to drain on shutdown, then Uvicorn
cancels them and runs the existing driver cleanup. Saved research history remains
on disk. Closing only the browser tab does not stop the server.

The dashboard is a minimal, single-loop research workspace: project upload, three
budget fields, current activity, and an experiment table with an evidence dialog.
It accepts a prepared research source folder and saves draft limits. Live baseline
import, Azure execution and Copilot credit metering remain pending, so **Start loop**
is disabled with an explanation. No uploaded ML code is executed by this UI slice.

In this Linux/WSL workspace, the optional web dependencies have been installed in
`.runtime/web-venv`. Start from the platform root:

```bash
PYTHONPATH=src .runtime/web-venv/bin/python -B -m research_intern.main serve
```

Open **http://127.0.0.1:8000**. Use `--port 8001` if 8000 is occupied. For another
Python environment, install the optional dependencies once with
`python -m pip install -e ".[web]"`, then use `research-intern serve`. Dependency
installation needs downloads; normal offline dashboard operation needs no network
services, account sign-in, Copilot usage, or Azure resources. Browser assets are
served locally, with no Node build, CDN, or external fonts.

Choose **Upload research folder**, selecting the project root. Include
`.research_intern/contract.yaml` when it is available. Source without a contract can
be uploaded for review; preparation stays blocked until the researcher defines the
objective and boundaries. The source is copied locally; it is not mounted or
synchronized with the original folder. The upload preserves relative file paths,
validates any supplied contract and its declared files before publishing, and refuses
to overwrite an existing project. Limits are 2,000 files and 64 MiB of source content. Keep datasets,
checkpoints, caches and virtual environments outside the selected source folder.
A folder upload transfers files; empty directories, permissions and symbolic links
are not preserved. Include hidden contract files and keep credentials separate.
The browser omits `.git` metadata from folder selection; direct API uploads must
exclude it. Original Git history is not imported. Baseline provenance still requires verification
during the future measured baseline import.

Once the contract is ready, choose **Prepare local workspace**. This runs bounded
Python/JSON/YAML checks without executing uploaded code, creates an independent Git
repository and records its exact initial source commit. Imported contents remain
unchanged. Ignored files are never force-added; remove caches/private files from the
source copy if preparation reports them. Existing Git metadata, unsupported links,
Git attributes/submodules, empty directories, files over 16 MiB and unexpected edits
block preparation. Repeating a successful preparation reuses its source commit.

Review **Evaluation setup**. The optional contract `evaluation` section records the
exact metric definition and scale, procedure, dataset version, evaluator entrypoint
and fixed split manifest. Pin the two local files using SHA-256; both are automatically
protected. On Linux use `sha256sum evaluate.py split_manifest.json`; on PowerShell use
`Get-FileHash -Algorithm SHA256` and lowercase the hash values. See
[EXPERIMENT_CONTRACT.md](Docs/EXPERIMENT_CONTRACT.md#optional-fixed-evaluation-protocol)
for the supported fields and output fingerprint requirement. Include helper modules,
reference annotations and other fixed inputs in the protected scope too.

Only choose **Confirm evaluation for this source** after the researcher agrees to
the displayed objective, procedure, data split and constraints. Uploading/preparing
does not imply agreement. If the metric is undecided, leave this unconfirmed and
review the uploaded repository first. Finalize the source contract before preparing;
changes after preparation are retained but require reconciliation. Interrupted Git
publication also blocks further setup for inspection instead of overwriting history.
No measured `EXP-000`, model training, dependency installation or live run occurs.
The coding-agent integration choice remains deferred.

Set **Azure compute (GPU-hours)**, **Experiments**, and **AI credits**, then
**Save limits**. These are persisted in `.runtime/research-project/settings.json`,
bound to the uploaded contract's digest; the original contract is not rewritten.
These are draft settings for a future live loop, not enforced Azure or billing limits.
Unknown compute/credit usage is displayed as unavailable. Changing the contract makes
the saved settings stale. Upload/settings changes and the web controller are serialized.

The UI has no run picker, scenario selector, preparation-limit input, chart or expanded
log panels. Before a project is uploaded, it can display the latest retained offline
loop, explicitly labelled as synthetic evidence. Uploading a project hides that
unrelated history. An active web simulation remains labelled as such. Simulation
creation is still available through the existing CLI/API for development.

Select an experiment number to open its parent, hypothesis, changes, objective deltas,
constraints, outcome, commit and job reference; diff and diagnostic data are collapsed.
Updates stream automatically. **Stop loop** permanently prevents further experiments;
already submitted work may still be collected. **Resume** is only shown for interrupted
work or pending collection. Closing the browser does not stop a web driver, and server
restart does not automatically resume one. The backend still drives one loop at a time.

Keep developing the ML repository independently. The agreed local handoff location is:

```text
ai-research-intern/
└── .runtime/                           ignored by the platform's Git
    ├── research-project/
    │   └── repository/                 copy of the independent ML repository
    │       └── .research_intern/contract.yaml
    └── simulations/
        └── loop-…/                     independent offline run
            ├── fixture_repo/           dedicated synthetic candidate repository
            └── ledger.sqlite3
```

Manual placement in that fixed location also remains supported; readiness updates
automatically. Use a source-only copy without `.git`. Source checking parses the
contract, declared paths, job YAML and any pinned evaluation inputs. Explicit preparation
adds Git and local preflight evidence; it does not validate a measured baseline,
prepare autonomous candidates, install ML dependencies or enable live runs. There is no arbitrary destination-path or project-replacement API.

The API binds to `127.0.0.1`, serves only local browser assets and run metadata, and
rejects foreign hosts and cross-origin mutation requests. It is a single-researcher
localhost application. Do not expose it as a hosted service. The API includes:

| Request | Purpose |
| --- | --- |
| `GET /api/health`, `GET /api/project` | Server activity and fixed project readiness |
| `GET /api/workspace`, `GET /api/workspace/events` | Single-workspace view and automatic updates |
| `POST /api/project/upload` | Bounded multipart folder upload to the fixed empty project slot |
| `POST /api/project/prepare` | Prepare an independent local Git workspace against the displayed contract digest |
| `POST /api/project/evaluation/confirm` | Confirm the reviewed protocol for its exact source commit and fingerprint |
| `POST /api/project/budget` | Save validated draft limits against the current contract digest |
| `POST /api/project/start` | Refuses live execution until its integrations are available |
| `GET /api/runs`, `POST /api/runs` | List recent offline runs or initialize one |
| `GET /api/runs/{id}` | Rebuilt state, budgets, experiment history and recent events |
| `POST /api/runs/{id}/resume` | Start/reconcile one background offline driver |
| `POST /api/runs/{id}/stop` | Persist the run's human stop |
| `GET /api/runs/{id}/experiments/{experiment_id}` | Detailed experiment evidence |
| `GET /api/runs/{id}/events` | Server-sent snapshots on change; reconnect reloads persisted events |
| `GET /api/openapi.json` | Machine-readable API specification |

POST requests need `X-Research-Intern: 1`. The browser supplies it automatically.
The driver activity field describes this server's worker; the controller field is
the last persisted observation. CLI drivers are protected by the same per-run OS
lock but are not advertised as live web workers. Use one driver interface at a time.

## 29. Testing

Prioritize deterministic components such as contract validation, permission checks, Git bookkeeping, evaluation rules, budgets, stopping logic, and research-state construction.
Mock Copilot and Azure in normal controller tests.
Tests should not consume real AI or GPU resources by default.
Real service tests should be explicit opt-in integration tests.

Run the current offline tests after the editable installation:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These tests mock the SDK client and use temporary fixtures. They do not start
Copilot, download a runtime, or submit Azure jobs. Use the workspace-local TEMP/TMP
settings above to keep their temporary files inside the workspace.

The execution-slice tests can also run independently of the Copilot SDK:

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -p 'test_execution_slice.py' -v
```

These checks cover scoring in both directions, invalid outputs, failure categories,
serial execution, durable job IDs, restart/idempotency behavior, immutable rules,
best-versus-latest tracking, and one real local Git fixture. They create fixtures
only in `.runtime/test-workspaces/` and require no downloads or service access.

Run the offline slices together (candidate/loop slices also use PyYAML;
API/server tests skip when their optional dependencies are absent):

```bash
PYTHONPATH=src python3 -B -m unittest discover -s tests -p 'test_*slice.py' -v
```

The handoff tests add research-memory reconstruction, negative evidence,
persisted limits/stops, fresh proposer instances, stale-plan rejection, and the
connected fixture demo. The full test command also includes the SDK-specific
spike tests and requires the installed Copilot SDK.

The loop tests add repeated candidate execution, bounded failed preparations,
stop/restart behavior, process locking, recovery around Git/reservation/submission,
download retries, and refusal to overwrite newer work. All use local fixtures and
simulated services.

To include the API and localhost server tests, install `.[web,web-test]` in the
selected environment. In the provisioned Linux environment:

```bash
PYTHONPATH=src:tests .runtime/web-venv/bin/python -B -m unittest discover -s tests -p 'test_*slice.py' -v
```

The web tests cover bounded creation, one active driver, persistent stop,
interrupted-run resume, contract readiness, detailed evidence, local request
boundaries and server-sent events. Folder-upload tests cover path confinement,
conflicts, size/count limits, interrupted bodies, atomic publication, overwrite refusal,
separate persisted budgets, stale contracts, and isolation of synthetic history. They use local fixtures and no external service.
The server smoke test binds a temporary loopback port. Sandboxes that prohibit
sockets or event-loop thread wakeups need permission to run these tests outside
that restriction. These tests do not validate visual rendering or browser JavaScript.

## 30. Failure and Recovery

Distinguish Copilot failure, candidate validation failure, Azure submission failure, Azure runtime failure, invalid outputs, evaluation failure, and model-quality regression.
Persist:

```text
experiment ID
selected parent
candidate commit
Azure job ID
lifecycle state
best experiment
budget usage
```

After restart, reconcile existing state before creating another candidate.
Avoid duplicate submissions and duplicate experiment IDs.

## 31. Security

Never commit or expose credentials.
Azure secrets must not appear in source code, experiment contracts, Copilot prompts, research state, ledger records, or Git history.
The backend owns authenticated Azure access.
Runtime agents receive narrow capabilities rather than raw credentials.

## 32. Explicit Non-Goals

The MVP is not building:

```text
universal autonomous science
full AutoML
multi-agent research organizations
parallel experiment scheduling
multi-user cloud hosting
GitHub/GitLab integration
generic cloud execution
every ML framework/logging adapter
microservices
distributed orchestration
vector memory
```

These exclusions are intentional.

## 33. Scientific Traceability

For every experiment the system must answer:

```text
What was the parent?
What hypothesis was tested?
What changed?
What exact code ran?
Which Azure job executed it?
What metrics returned?
Were constraints satisfied?
Was it kept, rejected, or failed?
What was learned?
```

This evidence chain is central to the product and demo.

## 34. Coding Agent Guidance

Coding agents must read `AGENTS.md` before substantial implementation.
Default rule:

> Build the smallest reliable vertical research loop before adding breadth.
> Prefer explicit typed Python, narrow interfaces, deterministic authority, and minimal dependencies.
> Do not redesign the project during ordinary implementation work.

## 35. Definition of MVP Success

The finished demonstration should look like:

```text
EXP-000 human baseline
↓
Copilot-generated EXP-001
↓
Azure ML execution
↓
machine evaluation
↓
ledger + research state
↓
Copilot-generated EXP-002
↓
...
↓
measurably improved best experiment
```

Every experiment must be traceable and declared constraints must remain satisfied.

## 36. Final Mental Model

The human provides:

```text
problem + experiment + objective
+ search boundaries + evaluator + compute
```

The AI Research Intern provides:

```text
reasoning + candidate generation
+ controlled code modification
+ execution orchestration
+ verification + memory + iteration
```

Build the smallest working version of that loop first.
