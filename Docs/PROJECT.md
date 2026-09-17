# AI Research Intern

## PROJECT.md

## 1. Project Purpose

AI Research Intern is an autonomous research optimization system for computational experiments.
It takes over after a human researcher already has a valid experiment, objective, evaluator, codebase, and execution environment.
Its job is to inspect evidence, propose changes, modify the experiment, execute it, evaluate results, remember what happened, and repeat.
The project is a research harness around an existing experiment, not the experiment itself.

```text
inspect → reason → propose → modify → execute → evaluate → learn → repeat
```

The MVP starts with machine learning because ML experiments are code-driven, executable, measurable, repeatable, and naturally suited to automated iteration.

## 2. Core Thesis

Current LLMs are not reliable enough to perform arbitrary open-ended science autonomously.
They become substantially more useful when research can be expressed as constrained search with machine-verifiable outcomes.
The project therefore targets **constrained, machine-evaluable optimization problems**.
A supported problem should have a clear objective, executable evaluator, explicit editable search space, explicit constraints, and a bounded experiment budget.
The LLM proposes hypotheses and candidate changes.
The evaluator decides whether they worked.
This separation is foundational.

## 3. Why the Scope Was Narrowed

The original idea was broader: build an autonomous AI scientist for open-ended research.
That creates a fragile chain:

```text
observation → diagnosis → hypothesis → experiment → interpretation
```

An early reasoning error can propagate while later outputs still sound plausible.
If success is subjective, the same model may effectively grade its own reasoning.
The MVP removes as much unverifiable scientific judgment as possible.
A candidate either improves the externally measured objective under the constraints or it does not.

## 4. Formal Problem Model

A supported research problem is represented as:

```text
(S, f, C, B)
```

`S` is the allowed solution/intervention space; `f` is the machine-executable objective; `C` is the constraints; `B` is the experiment/compute budget.
For maximization:

```text
x* = argmax f(x), x ∈ S, subject to C, within B
```

A candidate may involve code, configuration, model structure, data processing, training strategy, or several of these together.
The evaluator remains outside the agent's control.

## 5. Initial Domain: Machine Learning

The MVP targets ML optimization problems because ML gives us executable code, programmable evaluation, numeric metrics, remote compute, repeatable runs, and machine-readable artifacts.
Relevant problems include fine-tuning, distillation, quantization, transfer learning, loss minimization, accuracy improvement, latency optimization, regularization, augmentation, sampling, optimizer/scheduler changes, hyperparameter tuning, and architecture changes.
The long-term architecture is not limited to ML.
A future domain may fit if it exposes:

```text
candidate → executable experiment → machine-readable result → objective score
```

Possible later domains include algorithms, simulation-based physics, computational materials, computational biology, and control systems.
The generality comes from the experiment interface, not from assuming every domain works the same way.

## 6. What the Human Researcher Provides

The system assumes the researcher already has a meaningful working experiment.
The researcher provides: experiment codebase; dataset or access path; baseline experiment; objective metric and direction; optional target; editable areas; protected areas; constraints; experiment budget; and Azure execution configuration.
The researcher defines **where the system may search**, not **what diagnosis it should make**.
Example:

```text
Objective: maximize validation F1
Editable: model.py, train.py, configs/, augmentation
Protected: evaluate.py, test data
Constraint: latency <= 50 ms
Budget: 8 experiments, 6 GPU-hours
```

The researcher should not need to say "the model is overfitting, so increase dropout." Finding useful interventions is the Research Intern's job.

## 7. What the System Provides

The AI Research Intern provides project understanding, evidence inspection, hypothesis generation, experiment planning, candidate generation, controlled code modification, execution orchestration, deterministic evaluation, persistent experiment memory, search-state updates, stopping logic, and a visible research trail.
The mental model is:

```text
human provides the laboratory and objective
AI Research Intern provides the iterative research search process
```

The system does not redefine the research question.
It searches within the boundaries defined by the researcher.

## 8. Core Research Lifecycle

The conceptual lifecycle is:

```text
research state → observe → diagnose → hypothesize → plan
→ implement → validate → execute → collect → evaluate
→ record → update state → repeat
```

Reasoning-heavy steps may use an LLM: interpreting evidence, identifying bottlenecks, generating hypotheses, proposing structural changes, deciding which code is relevant, and explaining the candidate.
Deterministic software handles permissions, Git state, schema validation, budgets, Azure submission, polling, result collection, metric comparison, constraint checks, persistence, and stopping conditions.
The system should not become "a bunch of agents."
The LLM is one reasoning component inside a deterministic experimentation platform.

## 9. Role of GitHub Copilot

The MVP uses the official `github-copilot-sdk` Python package as the coding-agent runtime.
Copilot provides repository understanding, file navigation, editing, command execution, internal tool-use loops, session lifecycle, event streaming, and coding-agent behavior.
Our application owns the higher-level research lifecycle.

```text
Research Controller
→ start Copilot iteration
→ Copilot inspects/modifies candidate
→ Copilot finishes
→ Controller resumes deterministic workflow
```

Copilot does not own persistent research state, budget authority, Azure credentials, final scoring, protected-file policy, or global stopping decisions.

## 10. Clean Repository Decision

We are building AI Research Intern as a clean repository from scratch.
We are **not cloning `github/awesome-copilot` as the application base**.
`awesome-copilot` remains a reference for Copilot SDK/Ralph-loop mechanics such as session creation, fresh-context iterations, event handling, filesystem state, shutdown, and iterative loops.
Our product requires a different skeleton: research controller, Git experiment lifecycle, Azure ML, evaluator, ledger, budgets, recovery, and browser UI.
Starting clean avoids inheriting unrelated recipes, examples, directory structure, and assumptions.
For this MVP, the clean-repo approach is expected to reduce total implementation effort.

## 11. Experiment Repository Model

The experiment being optimized is logically separate from the AI Research Intern application.
The system operates on a local researcher-owned experiment repository or workspace.
The Research Intern application contains controller logic, Copilot integration, Git/workspace management, Azure integration, evaluator, ledger/state, schemas, backend API, and UI.
The experiment repository contains model code, training code, data access, evaluation code, configuration, Azure job definition, and Research Intern integration metadata.
The experiment should remain recognizable as an ordinary ML project.
The intended lock-in is only a small experiment contract.

## 12. Experiment Contract

The experiment contract is the formal interface between the researcher project and our system.
It defines objective, metric, direction, optional target, execution configuration, expected outputs, editable scope, protected scope, constraints, and budgets.
Conceptually:

```text
arbitrary experiment implementation
+
standard Research Intern interface
```

The experiment implementation may vary widely.
The contract should remain small, explicit, and stable.
Its exact schema belongs in `EXPERIMENT_CONTRACT.md`.

## 13. Standardized Experiment Outputs

For the MVP, every experiment should expose a known result structure:

```text
experiment_outputs/
├── run.json
├── metrics.json
├── metrics_history.json
├── logs/
└── artifacts/
```

`metrics.json` contains final structured metrics; `metrics_history.json` contains epoch/time-series values; `run.json` contains execution metadata and status.
Logs and artifacts provide supporting evidence.
Structured numeric data should drive evaluation; plots are mainly for humans.
This avoids building adapters for MLflow, TensorBoard, W&B, CSV, or arbitrary logging layouts during the hackathon.

## 14. Baseline and Experiment IDs

Autonomous research begins from a real human baseline:

```text
EXP-000 = baseline
```

The first autonomous modification is `EXP-001`; later iterations are `EXP-002`, `EXP-003`, and so on.
Bootstrap or project-integration work happens before `EXP-000` and is not an experiment.
Each experiment is a scientific record, not merely a code version.
It includes parent experiment, hypothesis, candidate change, rationale, code state, code diff, Azure job, metrics, constraint results, decision, and conclusion.

## 15. Git and Scientific History

Git and the experiment ledger have different responsibilities.
Git stores exact historical code states.
The ledger stores scientific meaning.

```text
Git: experiment code state
Ledger: experiment ↔ parent ↔ hypothesis ↔ Azure run ↔ metrics ↔ conclusion
```

For the serial MVP, use one reusable experiment working directory with one Git commit per experiment.
If an experiment regresses, reset the workspace to the selected parent commit before generating the next candidate.
Patches may also be stored for audit and UI display.

## 16. Experiment Outcomes

A completed run can end in one of four meaningful states.
**Improved:** satisfies constraints and improves the objective; may become the new best.
**Regressed:** completes but performs worse; rejected as active parent but retained as evidence.
**Failed:** execution fails due to code errors, invalid configuration, CUDA OOM, NaNs, checkpoint issues, or Azure failure.
**Goal reached:** satisfies the target objective and constraints; the autonomous loop may stop.
Negative and failed experiments remain in research memory because they contain useful evidence about where not to search.

## 17. Experiment Memory

The permanent source of truth is the experiment ledger.
The ledger preserves:

```text
experiment ↔ parent ↔ code commit ↔ Azure job
↔ metrics ↔ hypothesis ↔ conclusion
```

The full history should not be placed into every LLM prompt.
Instead, construct a compact `research_state` containing objective, baseline, best experiment, latest experiment, promising directions, rejected directions, open questions, recent results, and remaining experiment/GPU/AI budgets.
The ledger provides continuity between research iterations.
Copilot chat history does not need to.

## 18. Fresh Copilot Sessions

The preferred MVP pattern is a fresh Copilot session for each experiment iteration.
Each session receives the project objective, experiment contract, current research state, selected parent, relevant metrics/logs, and current code state.
This avoids one indefinitely growing conversation.
Persistent knowledge lives on disk and in the ledger rather than only inside model context.
Fresh sessions also make iterations easier to reproduce, inspect, and recover.
The controller decides when a session begins and ends.

## 19. Search Strategy

The first MVP does not need a sophisticated search algorithm.
The initial goal is to prove the complete experiment loop, so early versions may let the LLM propose one candidate at a time.
Later, the search layer can combine:

```text
LLM proposer + classical optimizer
```

The LLM is useful for semantic and structural changes such as architecture, augmentation, loss functions, training strategy, and new candidate families.
Classical methods such as TPE, Bayesian optimization, or evolutionary search can later optimize numerical parameters such as learning rate, weight decay, dropout, rank, or batch size.
The LLM should be treated as an intelligent proposal mechanism, not necessarily the complete optimizer.

## 20. Azure AI and Azure ML Are Separate

There are two logically separate compute paths.
Model reasoning happens through the Copilot/model runtime.
Experiment execution happens through Azure ML.
Azure ML is the laboratory for GPU-heavy training, fine-tuning, quantization, or evaluation.
Once a job is submitted, the LLM should not remain active waiting for it.

```text
submit job → store ID → poll → collect outputs
→ evaluate → update ledger → next Copilot iteration
```

Mental model: **Copilot = researcher; Azure ML = laboratory; Controller = coordinator.**

## 21. Local-First MVP

The MVP is intentionally local-first.

```text
VS Code
+
local Python application
+
local experiment repository
+
Copilot SDK
+
Azure ML
```

A browser UI may run on localhost and communicate with a local Python backend.
The backend owns filesystem access, research state, Azure integration, and Copilot lifecycle.
For the hackathon we do not need GitHub OAuth, remote Research Intern hosting, multi-user infrastructure, or organization-wide auth flows.
Those are post-MVP concerns.

## 22. Product Experience

The experience should feel like starting an autonomous research run, not repeatedly chatting with Copilot.
The human provides the mission and boundaries; the system performs the iterations.
The eventual browser UI should expose objective, baseline, current best, current experiment, status, budget usage, code diff, Azure status, metrics, lineage, accumulated findings, and agent activity.
VS Code remains the development environment.
The browser acts as research mission control.
The UI is valuable for the demo but is not the first technical milestone.

## 23. Safety and Permission Boundary

The agent must not be able to change the definition of success.
Protected surfaces include objective, evaluator, protected test data, constraints, budgets, and contract-controlled policy.
Editable and protected scope must be explicit.
The LLM should receive capability-limited tools rather than unrestricted infrastructure credentials.

```text
submit_job()
get_job_status()
collect_results()
```

The deterministic controller remains the authority for permissions, credentials, budgets, and final evaluation.

## 24. Evaluation Integrity

Machine evaluation is stronger than LLM self-evaluation, but repeated search can still overfit a validation metric.
The longer-term design therefore separates:

```text
search evaluator
```

from:

```text
final audit evaluator
```

A final candidate may be checked with repeated seeds, held-out evaluation, reproducibility checks, and final constraint verification.
The autonomous loop optimizes the search metric.
The audit determines whether the final improvement is credible.

## 25. Bounded Autonomy

Autonomy must operate inside explicit budgets.
The controller may enforce maximum experiment count, maximum Azure GPU-hours, Copilot/AI usage cap, wall-clock limit, and optional stagnation threshold.
Stopping conditions may include:

```text
target reached
OR experiment budget exhausted
OR GPU budget exhausted
OR AI budget exhausted
OR stagnation detected
OR human stops run
```

The LLM does not have authority to exceed these limits.
Budget enforcement is deterministic.

## 26. MVP Success Criteria

The hackathon should prove one central claim:

> An AI agent can autonomously improve an existing ML experiment through multiple traceable, machine-evaluated iterations.
> A strong demo begins with an intentionally improvable baseline.

```text
baseline mAP = 0.580
target mAP   = 0.630
latency      <= 50 ms
max trials   = 8
```

The demo should show multiple autonomous experiments, at least one rejected or failed direction, at least one useful improvement, code changes tied to experiments, Azure execution, objective evaluation, persistent experiment history, and a final result meaningfully better than baseline.
The research loop matters more than the number of integrations.

## 27. What We Are Not Building

For the MVP, we are not building a universal autonomous scientist, general AutoML platform, arbitrary scientific reasoning infrastructure, arbitrary cloud backends, GitHub/GitLab integration, multi-user hosting, microservices, every ML logging/framework adapter, complex multi-agent organizations, parallel experiment scheduling, or a vector database.
We are also not allowing the LLM to judge its own success.
These exclusions are deliberate.
They keep engineering effort concentrated on the research loop.
The coding agent should not expand these boundaries unless `MVP_SCOPE.md` is explicitly changed.

## 28. Engineering Philosophy

Build the system as a modular monolith.
Prefer simple interfaces, explicit ownership, and deterministic infrastructure.
Avoid premature abstractions and broad integrations before the core loop works.
Build vertically.
The first meaningful milestone is:

```text
load experiment → invoke Copilot → make one controlled modification
→ commit candidate → execute on Azure → collect metrics
→ evaluate → record EXP-001
```

Once one complete experiment works reliably, automate repetition.
Only then add smarter search, richer recovery, and UI polish.

## 29. Current Project State

The project now has an offline implementation of contract loading, controlled
candidate editing and Git versioning, simulated execution, deterministic evaluation,
ledger persistence, research-state handoff, bounded repetition, basic recovery,
and localhost API/browser mission control for simulated runs.
Local source upload, independent Git workspace preparation, and explicit evaluation
protocol confirmation are also available without signing in to services. Preparation
preserves exact source and protects pinned evaluator/split files; it does not create
a measured baseline or execute ML code. The actual research metric remains a human
decision. A Copilot SDK spike exists but live access has not been validated; agent
integration is currently deferred pending a researcher decision. Real ML workload
results, measured baseline import, live research editing, Azure execution,
and repetition over real ML experiments remain unfinished. See `README.md` and `current_state.md` for the
current commands, verified tests, implementation limits, and next milestone.
This document is the conceptual source of truth for what the project is and why it exists.

## 30. Documentation Set

The project intentionally uses seven core Markdown files:

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

`PROJECT.md` defines the thesis and product concept; `MVP_SCOPE.md` defines the hackathon boundary; `ARCHITECTURE.md` defines technical ownership; `RESEARCH_LOOP.md` defines the lifecycle; `EXPERIMENT_CONTRACT.md` defines the experiment interface; `AGENTS.md` guides the coding agent; `README.md` provides developer orientation.
These documents must remain mutually consistent.

## 31. Non-Negotiable Design Principles

1. **External evaluation over self-evaluation.** The agent proposes; the evaluator decides.
2. **Persistent state over conversational memory.** Research knowledge lives in durable project state.
3. **Standardize the environment.** Keep the experiment interface predictable.
4. **Generalize the researcher.** Let Copilot reason across valid experiments inside the contract.
5. **Deterministic infrastructure.** Execution, permissions, scoring, budgets, and persistence are ordinary software.
6. **Controlled autonomy.** The agent searches only inside explicitly permitted boundaries.
7. **Scientific traceability.** Every conclusion maps to an experiment, code state, execution, and result.
8. **Build the loop first.** One complete research cycle matters more than broad feature coverage.

## 32. Final Mental Model

The human supplies:

```text
problem + experiment + objective + search boundaries + evaluator + compute
```

The AI Research Intern supplies:

```text
reasoning + candidate generation + code modification
+ execution orchestration + verification + memory + iteration
```

The architecture can be summarized as:

```text
LLM intelligence + controlled search + external evaluator + persistent experiment memory
```

The product is best understood as:

> **A deterministic experimentation platform with an LLM-powered research and search engine inside it.**
> The MVP exists to prove that this architecture can autonomously produce measurable, traceable, and reproducible improvement on a real ML experiment.
