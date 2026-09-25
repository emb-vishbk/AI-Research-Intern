# AI Research Intern

## AGENTS.md

## 1. Purpose

This file is the operating guide for coding agents working on the AI Research Intern repository.

Read it before making substantial changes.

The goal is to build the smallest reliable implementation of the architecture documented in `docs/`.

Do not redesign the project unless explicitly requested.

Do not expand MVP scope merely because an additional feature appears useful.

---

## 2. What You Are Building

AI Research Intern is an autonomous optimization system for computational ML experiments.

A human researcher already has:

* an ML experiment,
* code,
* data access,
* an evaluator,
* an objective,
* Azure ML execution,
* and a baseline.

Our system takes over after that point.

Its core loop is:

```text
inspect evidence
→ propose candidate
→ modify code
→ validate
→ execute on Azure
→ evaluate externally
→ record result
→ update research state
→ repeat
```

The LLM proposes.

The evaluator decides.

---

## 3. MVP Goal

The MVP must prove:

> An AI coding agent can autonomously improve an existing ML experiment through repeated, machine-evaluated, traceable experiments.

Success means demonstrating several autonomous iterations from a baseline toward a measurable objective.

Breadth of integrations is secondary.

A reliable vertical research loop is the priority.

---

## 4. Documentation Set

The repository uses seven primary Markdown documents:

```text
AGENTS.md
README.md

docs/
├── PROJECT.md
├── MVP_SCOPE.md
├── ARCHITECTURE.md
├── RESEARCH_LOOP.md
└── EXPERIMENT_CONTRACT.md
```

Do not create additional architecture documents unless clearly necessary.

Prefer updating an existing authoritative document.

---

## 5. Document Responsibilities

`PROJECT.md`

Defines what the project is and why it exists.

`MVP_SCOPE.md`

Defines what belongs in the hackathon MVP and what does not.

`ARCHITECTURE.md`

Defines software boundaries, modules, dependencies, persistence, and infrastructure ownership.

`RESEARCH_LOOP.md`

Defines runtime experiment-to-experiment behavior.

`EXPERIMENT_CONTRACT.md`

Defines the interface between researcher experiments and the Research Intern.

`README.md`

Provides concise developer onboarding and usage instructions.

`AGENTS.md`

Defines how coding agents should work inside this repository.

---

## 6. Resolving Documentation Conflicts

When documents appear inconsistent, use this priority:

```text
MVP_SCOPE.md
↓
ARCHITECTURE.md
↓
RESEARCH_LOOP.md
↓
EXPERIMENT_CONTRACT.md
↓
PROJECT.md
↓
README.md
```

`MVP_SCOPE.md` wins on whether something should exist now.

`ARCHITECTURE.md` wins on technical ownership and boundaries.

`RESEARCH_LOOP.md` wins on lifecycle behavior.

`EXPERIMENT_CONTRACT.md` wins on researcher-experiment integration.

Do not silently resolve meaningful conflicts.

Surface them before introducing architectural changes.

---

## 7. Current Project State

The modular monolith includes a shared live/offline serial controller, restricted
Copilot editing, Azure execution/recovery, measured baseline, independent scoring,
service reservations, SQLite/Git memory and a localhost browser UI.

See README.md and current_state.md for implemented entry points and the offline
test runner. Historical milestone instructions below describe the build sequence,
not missing work. A real configured multi-trial cloud demonstration remains an
external acceptance check; ordinary tests must continue to use fake services.

Inspect the repository before every implementation task.

---

## 8. Repository Strategy

This project is being built from scratch as a clean repository.

Do not clone `github/awesome-copilot` into this project.

`awesome-copilot` is a reference only.

Useful ideas may be borrowed from its official Python Copilot SDK / Ralph-loop examples.

Relevant patterns include:

```text
client startup
session creation
working-directory configuration
event subscription
prompt submission
fresh iterations
session shutdown
```

Do not inherit unrelated cookbook structure.

---

## 9. Core Architectural Principle

Think of the system as:

> A deterministic experimentation platform with an LLM-powered research engine inside it.

The LLM should not own everything.

Copilot handles semantic reasoning and coding.

Ordinary software owns orchestration and authority.

---

## 10. Responsibility Boundary

Copilot may own:

```text
repository understanding
evidence interpretation
diagnosis
hypothesis generation
candidate planning
controlled code modification
```

Deterministic software owns:

```text
permissions
Git state
experiment IDs
budgets
Azure credentials
Azure execution
result validation
scoring
ledger persistence
stopping conditions
recovery
```

Never blur this boundary without a concrete reason.

---

## 11. Three Main Systems

Keep these conceptually separate:

```text
1. AI Research Intern application
2. Researcher's experiment repository
3. Azure ML execution environment
```

The Research Intern orchestrates.

The experiment repository contains the scientific workload.

Azure ML executes computational experiments.

---

## 12. Runtime Copilot Is Not This Coding Agent

Do not confuse two different roles.

You are the coding agent building the AI Research Intern application.

The application will later invoke Copilot SDK sessions as the runtime research agent.

Those runtime sessions operate on researcher experiments.

Your job is to implement the platform that controls them.

---

## 13. Target Repository Shape

The intended structure is approximately:

```text
ai-research-intern/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── docs/
├── schemas/
├── src/
│   └── research_intern/
│       ├── main.py
│       ├── api/
│       ├── controller/
│       ├── copilot/
│       ├── contracts/
│       ├── domain/
│       ├── evaluation/
│       ├── execution/
│       ├── ledger/
│       ├── validation/
│       └── workspace/
├── tests/
└── ui/
```

Exact filenames may evolve.

Module ownership should remain consistent with `ARCHITECTURE.md`.

---

## 14. Use a Modular Monolith

The MVP is a modular monolith.

Do not introduce:

```text
microservices
message brokers
distributed workers
Kubernetes
service meshes
distributed databases
```

Direct Python composition is preferred.

The application should remain easy to run locally.

---

## 15. Build Vertically

Do not fully implement every subsystem before testing anything.

Build one complete slice at a time.

The implementation sequence is:

```text
1. Copilot SDK spike
2. local candidate modification
3. Git candidate lifecycle
4. one Azure experiment
5. evaluation + ledger
6. research-state handoff
7. autonomous repetition
8. minimal browser UI
```

A working end-to-end slice is more valuable than many incomplete modules.

---

## 16. Immediate First Milestone

The first implementation milestone is intentionally small.

Prove:

```text
Python application
→ start Copilot SDK client
→ create a session
→ point it at a controlled working directory
→ send a simple task
→ receive events/result
→ close session cleanly
```

Do not begin with Azure, SQLite, FastAPI, or the full research loop.

Establish the Copilot primitive first.

---

## 17. Second Milestone

After the Copilot spike works:

```text
prepare controlled Git repo
→ choose parent state
→ invoke Copilot
→ modify one allowed file
→ inspect actual diff
→ validate change
→ commit candidate
```

This proves controlled code mutation.

Do not add autonomous repetition yet.

---

## 18. Third Milestone

Then prove one real experiment lifecycle:

```text
candidate commit
→ Azure ML submission
→ job ID persistence
→ polling
→ standardized result download
→ deterministic evaluation
→ EXP-001 ledger record
```

Only after this works should the outer loop become autonomous.

---

## 19. Fourth Milestone

Add experiment-to-experiment continuity:

```text
EXP-001 result
→ ledger update
→ research_state
→ fresh Copilot session
→ EXP-002 candidate
```

At this point the core thesis begins to exist.

The UI can follow afterward.

---

## 20. Copilot Integration Rules

All `github-copilot-sdk` usage belongs behind a small adapter.

Prefer an interface conceptually like:

```text
run_iteration(
    context,
    working_directory
) -> CandidateResult
```

Do not spread SDK-specific logic across the codebase.

Session creation, events, completion, and shutdown belong in the Copilot integration layer.

---

## 21. Fresh Sessions

Runtime research should use a fresh Copilot session for each experiment iteration.

Do not depend on one giant persistent conversation.

Each session should receive:

```text
objective
experiment contract
research state
selected parent
relevant metrics
relevant logs
remaining budget
implementation task
```

Persistent knowledge lives outside conversation memory.

---

## 22. Structured Copilot Output

Do not depend only on prose responses.

Capture structured research decisions where practical.

A candidate should eventually contain:

```text
parent experiment
observation
diagnosis
hypothesis
planned intervention
expected effect
files changed
```

Do not request or persist private chain-of-thought.

Persist concise research rationale and decisions.

---

## 23. Experiment Contract

The runtime experiment interface is defined in `EXPERIMENT_CONTRACT.md`.

Do not invent framework-specific assumptions.

The Research Intern should depend primarily on:

```text
objective
execution configuration
editable scope
protected scope
standardized outputs
constraints
budgets
```

Internal experiment structure may vary.

---

## 24. Protected Surfaces

Protected files are a hard boundary.

The runtime agent must not modify:

```text
evaluator
held-out test data
objective
constraints
budget
effective experiment contract
researcher-declared protected paths
```

Prompt instructions are not sufficient protection.

Always verify the actual Git/filesystem diff deterministically.

---

## 25. Git Model

Git stores exact experiment code states.

The experiment ledger stores scientific meaning.

Never equate:

```text
Git commit = complete experiment record
```

Instead:

```text
experiment
↔ Git commit
↔ Azure job
↔ metrics
↔ hypothesis
↔ decision
```

Both Git and the ledger are required.

---

## 26. Workspace Strategy

The MVP uses one reusable experiment working directory.

Do not create a permanent worktree for every experiment.

Typical runtime flow:

```text
select parent commit
→ reset dedicated experiment workspace
→ Copilot modifies
→ validate
→ commit EXP-N
→ execute
```

Parallel worktrees are post-MVP.

---

## 27. Git Safety

Be conservative around destructive Git operations.

The runtime may eventually need operations such as reset/checkouts.

Those operations must only target the dedicated experiment repository.

Never run destructive Git operations blindly against the AI Research Intern development repository.

Verify repository path and expected state first.

---

## 28. Experiment IDs

Use deterministic experiment identifiers:

```text
EXP-000
EXP-001
EXP-002
...
```

`EXP-000` is the human baseline.

`EXP-001` is the first autonomous experiment.

Bootstrap/setup work is not an experiment.

Experiment IDs must never be reused.

---

## 29. Azure ML Boundary

Azure ML is the experiment laboratory.

The Azure adapter should eventually expose narrow operations:

```text
submit_job(...)
get_status(...)
download_outputs(...)
cancel_job(...)
```

Do not allow Copilot to construct Azure clients directly.

Do not give Copilot Azure credentials.

---

## 30. Azure Waiting Behavior

The LLM should not remain active while a GPU experiment runs.

Required model:

```text
Copilot generates candidate
→ Copilot session ends
→ Azure runs
→ controller polls
→ result arrives
→ evaluator runs
→ next Copilot session begins
```

Do not waste model usage waiting for remote computation.

---

## 31. Standard Experiment Outputs

Runtime experiments should produce:

```text
experiment_outputs/
├── run.json
├── metrics.json
├── metrics_history.json
├── logs/
└── artifacts/
```

Structured values are authoritative.

Do not build metric extraction around arbitrary console logs.

Do not infer scores from plots when structured metrics exist.

---

## 32. Evaluator Authority

The evaluator is deterministic.

Copilot must never decide:

```text
"My experiment succeeded."
```

The evaluator compares recorded values against:

```text
objective
direction
parent
current best
constraints
target
```

Typical decisions are `KEEP`, `REJECT`, `FAILED`, and `GOAL_REACHED`.

---

## 33. Experiment Ledger

Use:

```text
SQLite
+
filesystem
+
Git
```

SQLite stores structured research metadata.

Filesystem stores logs, diffs, results, and larger artifacts.

Git stores historical code state.

Do not add a vector database for the MVP.

---

## 34. Research State

The ledger is permanent memory.

`research_state` is compact working memory.

Research state should contain information such as:

```text
objective
baseline
current best
last experiment
recent results
supported directions
rejected directions
known failures
open questions
remaining budget
```

It must be reconstructable from persisted history.

---

## 35. Failure Semantics

Do not collapse all failures into one category.

Distinguish:

```text
Copilot failure
candidate validation failure
Azure submission failure
Azure runtime failure
output-contract failure
evaluation failure
model-quality regression
```

A training crash is not the same as a scientifically poor result.

Persist enough evidence to diagnose each case.

---

## 36. Recovery

Design state transitions so the application can eventually recover after interruption.

Persist critical anchors before long operations.

In particular, store the Azure job ID immediately after successful submission.

On restart, reconcile existing state before creating another experiment.

Avoid duplicate submissions and duplicate experiment IDs.

---

## 37. FastAPI Rules

FastAPI is transport infrastructure.

Keep route handlers thin.

Routes may trigger or query application services.

Do not place the research loop inside API handlers.

Do not make HTTP concepts leak deeply into domain logic.

---

## 38. Browser UI Rules

The browser is research mission control.

It should eventually display:

```text
objective
baseline
best experiment
budget
current status
current hypothesis
experiment history
code diff
Azure job
metrics
decision
conclusion
```

The browser does not make scientific decisions.

Do not prioritize UI polish before the backend research loop works.

---

## 39. Event Streaming

When UI work begins, expose meaningful lifecycle events.

Examples:

```text
copilot_started
candidate_planned
files_modified
preflight_passed
candidate_committed
azure_submitted
azure_running
results_collected
experiment_evaluated
research_state_updated
```

Tool/activity events are preferred over dependence on raw reasoning streams.

---

## 40. Python Style

Prefer readable, typed Python.

Use small modules with explicit responsibilities.

Prefer `pathlib.Path` for filesystem paths.

Use Pydantic where external data/schema validation is useful.

Use dataclasses or simple classes for lightweight internal domain objects where appropriate.

Avoid abstractions that do not yet solve a real problem.

---

## 41. Async Code

Use async code when required by the SDK, event streaming, or I/O lifecycle.

Do not make the entire codebase asynchronous merely because one dependency is async.

Keep synchronous deterministic logic synchronous when simpler.

Make concurrency explicit.

The MVP is serial.

---

## 42. Error Handling

Fail explicitly.

Prefer domain-specific exceptions or structured failure results where they improve clarity.

Do not silently swallow exceptions.

Do not convert infrastructure failure into a misleading research decision.

Logs should contain enough context to diagnose failures.

User-facing errors should remain concise.

---

## 43. Logging

Use standard Python logging unless a concrete need requires more.

Include identifiers such as:

```text
experiment_id
Azure job ID
lifecycle state
```

Avoid logging secrets.

Operational logs are not the experiment ledger.

The ledger is the scientific record.

---

## 44. Secrets

Never commit secrets.

Never place Azure credentials inside:

```text
source files
experiment contract
Copilot prompt
research state
ledger records
Git history
```

Use authenticated SDK/environment mechanisms.

Do not print tokens or credential objects.

---

## 45. Dependencies

Add dependencies only when required by the current vertical milestone.

Do not introduce large frameworks speculatively.

For the initial implementation, do not add:

```text
LangChain
LangGraph
Microsoft Agent Framework
Optuna
Celery
Redis
vector databases
```

unless a later concrete requirement justifies them.

The Copilot SDK already provides the runtime agent loop we need.

---

## 46. Search Strategy

Do not implement advanced search initially.

The first research controller can use:

```text
research state
→ Copilot proposes one candidate
→ execute
→ evaluate
→ repeat
```

Classical optimization can be added later.

Do not delay the autonomous loop for Bayesian optimization or evolutionary search.

---

## 47. Multi-Agent Systems

The MVP is single-agent from the research-reasoning perspective.

Do not create planner/reviewer/executor agent hierarchies.

Separate software modules do not imply separate LLM agents.

Use deterministic components whenever semantic reasoning is unnecessary.

Only introduce additional agents after evidence shows they are needed.

---

## 48. Testing Strategy

Prioritize deterministic components.

Unit-test:

```text
contract validation
permission checks
Git bookkeeping
evaluation logic
budget accounting
stopping conditions
research-state construction
```

Mock Copilot and Azure in controller tests.

Tests must not consume real AI or GPU resources by default.

---

## 49. Integration Testing

Eventually maintain one vertical integration path:

```text
baseline
→ mock Copilot candidate
→ candidate commit
→ mock Azure result
→ deterministic evaluation
→ ledger record
→ rebuilt research state
```

Real Copilot/Azure tests should be explicit opt-in tests.

Never make ordinary unit tests depend on network services.

---

## 50. Schemas

Keep machine-readable schemas under `schemas/`.

Expected initial schemas include:

```text
contract.schema.json
run.schema.json
metrics.schema.json
```

A candidate-plan schema may be added when needed.

Do not build an elaborate schema ecosystem before these core interfaces exist.

---

## 51. Configuration

Keep application configuration separate from experiment-contract configuration.

Application configuration concerns the Research Intern runtime.

Experiment contract concerns the scientific experiment boundary.

Do not mix infrastructure credentials into contract files.

Prefer explicit configuration over hidden globals.

---

## 52. Documentation Updates

Update documentation when implementation introduces a real architectural decision.

Do not rewrite architecture documents for every internal refactor.

If code behavior intentionally diverges from an authoritative document, update the relevant document in the same change.

Never allow docs and architecture to silently drift.

Keep additions concise.

---

## 53. Avoid Premature Generality

The hackathon uses one prepared ML experiment.

Do not solve arbitrary repository onboarding before that experiment works.

Do not create adapters for every ML framework.

Do not create a generic cloud execution abstraction.

Do not design for every future scientific discipline now.

Prove the core interface first.

---

## 54. Avoid Premature Production Features

Do not implement:

```text
multi-user tenancy
hosted OAuth account systems
custom organization authentication
RBAC
remote hosted backend
distributed scheduling
parallel GPU experiments
enterprise observability
```

These are outside the MVP.

Localhost is acceptable.

One researcher is acceptable.

---

## 55. Keep the Experiment Ordinary

Do not force researcher code to inherit framework base classes.

Do not require the experiment to be rewritten around our package.

The intended integration is a small contract plus standardized outputs.

The researcher should retain control over the internal project structure.

Our framework adapts at the boundary.

---

## 56. Scientific Traceability

Every executed experiment must eventually answer:

```text
What was the parent?
What hypothesis was tested?
What changed?
What exact code ran?
Which Azure job executed it?
What metrics returned?
Were constraints satisfied?
What was the decision?
What was learned?
```

Do not sacrifice this traceability for implementation convenience.

It is central to the product.

---

## 57. Negative Results

Do not delete or forget poor experiments.

A rejected candidate still contains information.

A failed candidate may reveal implementation or resource constraints.

The research state should use negative evidence to avoid blind repetition.

The ledger preserves both successful and unsuccessful experiments.

---

## 58. Current Best Versus Latest

Never assume the latest experiment is the best experiment.

Maintain these concepts separately:

```text
last_experiment
best_experiment
selected_parent
```

A rejected experiment may be the latest while an older experiment remains best.

Parent selection should respect this distinction.

---

## 59. Authority Hierarchy

The runtime authority order is:

```text
human-defined contract
↓
deterministic controller
↓
deterministic evaluator
↓
Copilot proposal
```

The model may recommend.

It may not override contract boundaries.

It may not override evaluator results.

It may not override stopping rules.

---

## 60. When Implementing a Task

Before coding:

```text
1. inspect current repository
2. read the relevant authoritative docs
3. identify the smallest required change
4. preserve module boundaries
```

Then implement.

Run the smallest relevant verification.

Report what changed and any remaining blocker.

---

## 61. When Requirements Are Ambiguous

First infer from the existing documentation.

Prefer the interpretation that:

```text
keeps the MVP smaller
preserves existing architecture
reduces new dependencies
keeps deterministic authority outside the LLM
```

Do not invent major subsystems to resolve minor ambiguity.

If a decision would materially change architecture, surface it explicitly.

---

## 62. When Refactoring

Do not refactor merely because a different abstraction looks cleaner.

Refactor when existing structure creates a concrete implementation problem.

Preserve behavior and tests.

Avoid large unrelated cleanup during feature work.

Keep diffs narrow enough to review.

---

## 63. When Adding a Dependency

Before adding one, ask:

```text
Does the current milestone actually require this?
Can Python standard library solve it adequately?
Does an existing dependency already provide it?
Does this move us closer to the vertical MVP?
```

If not, do not add it.

Dependency count is not a measure of architectural quality.

---

## 64. When Considering an External Repository

Do not change the project base.

Do not copy large codebases into the repository.

External repositories may be studied for small implementation patterns.

Port only the minimal mechanism needed.

The AI Research Intern remains our own clean application.

---

## 65. Definition of Good MVP Code

Good code for this project is:

```text
small
explicit
typed
testable
recoverable
traceable
easy to delete or replace
```

It does not need to anticipate every post-hackathon feature.

Prefer working boundaries over sophisticated abstractions.

Prefer readable control flow over cleverness.

---

## 66. Non-Negotiable Invariants

Do not violate these:

1. The LLM never decides final experimental success.
2. The LLM never owns Azure credentials.
3. The LLM never owns permanent research memory.
4. Protected paths are enforced deterministically.
5. Every executed experiment maps to an exact Git state.
6. Every evaluated experiment maps to a ledger record.
7. Research state derives from persistent history.
8. Azure ML is infrastructure, not an agent.
9. The MVP runs experiments serially.
10. The controller owns continuation and stopping.
11. The experiment contract defines the research boundary.
12. The system must remain recoverable and traceable.

---

## 67. Scope Guard

When tempted to add functionality, ask:

> Does this directly help us autonomously move from `EXP-N` to a better `EXP-N+1`?

If no, defer it.

The core innovation is the research loop.

Do not spend the hackathon rebuilding existing development tools or infrastructure.

---

## 68. Immediate Development Direction

Continue from the implemented vertical loop. Inspect the current source and test
results, preserve its deterministic boundaries and fix demonstrated gaps. Real
service acceptance requires an approved workload, authentication and numeric limits;
do not invent these or turn fake-service tests into paid integration tests.

---

## 69. Final Mental Model

You are building this:

```text
Human defines problem and boundaries
↓
Deterministic Research Controller
↓
Copilot reasons and modifies candidate
↓
Deterministic validation
↓
Git records exact code
↓
Azure ML executes
↓
Deterministic evaluator scores
↓
Ledger records evidence
↓
Research state summarizes learning
↓
Fresh Copilot iteration
↺
```

Keep the system centered on this loop.

Everything else is supporting infrastructure.

Build the smallest reliable version of it first.
