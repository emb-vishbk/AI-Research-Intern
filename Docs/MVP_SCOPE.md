# AI Research Intern

## MVP_SCOPE.md

## 1. Purpose

This document defines the exact hackathon MVP boundary for AI Research Intern.
It exists to prevent scope drift and overengineering.
The MVP is not a production platform; it is a focused proof of the autonomous research loop.
Treat this file as authoritative for what must be built now versus deferred.
If a feature does not materially strengthen the MVP thesis, defer it.

## 2. MVP Thesis

Implementation status (2026-09-17): the platform connects baseline measurement,
bounded live coding/execution, independent evaluation, durable memory/recovery and
browser controls. Offline service-fake tests verify this composition. The thesis
below still requires a real approved multi-trial run demonstrating measured
improvement; passing platform tests alone does not establish scientific success.

The MVP must prove one claim:

> An AI coding agent can autonomously improve an existing ML experiment through repeated, machine-evaluated, traceable experimentation.
> The researcher supplies a valid experiment, objective, evaluator, search boundaries, constraints, budgets, and Azure execution.
> The system proposes, implements, executes, evaluates, remembers, and iterates.
> The important result is measurable autonomous improvement, not breadth of integrations.

## 3. Supported Problem Class

The MVP supports constrained, machine-evaluable optimization problems.
A valid problem has an explicit objective, executable experiment, machine-verifiable evaluator, editable search space, protected surfaces, enforceable constraints, and bounded budget.
The first domain is machine learning.
Suitable examples include fine-tuning, distillation, quantization, loss minimization, accuracy improvement, augmentation, regularization, optimizer/scheduler changes, hyperparameter tuning, and model architecture changes.
Open-ended scientific questions without an external evaluator are outside scope.

## 4. Human/System Boundary

The human defines the research problem and supplies the experiment codebase, dataset access, evaluator, Azure configuration, objective, constraints, permissions, budgets, and baseline.
The researcher defines where the agent may search, not what diagnosis it should make.
The Research Intern owns iterative improvement after that point.
It inspects evidence, forms hypotheses, modifies permitted code, executes candidates, evaluates results, records evidence, and chooses future experiments.
The system does not redefine the research question or definition of success.

## 5. Core Research Loop

The required lifecycle is:

```text
research state
→ observe
→ diagnose
→ hypothesize
→ plan
→ implement
→ validate
→ execute
→ collect
→ evaluate
→ record
→ update state
→ repeat
```

The loop stops when the target is reached or a declared stopping boundary is hit.

## 6. Demo Requirement

Use one real ML experiment with meaningful room for improvement.
Do not choose an already saturated baseline.
A representative target could be:

```text
baseline mAP = 0.580
target mAP   = 0.630
latency      <= 50 ms
max trials   = 8
```

The exact metric may differ; progress must be externally measurable.
The demo should show multiple autonomous iterations, including at least one rejected or failed direction.

## 7. Local-First Boundary

The MVP runs locally on the researcher's machine.
The intended environment is:

```text
VS Code
+ local Python backend
+ local experiment Git repository
+ GitHub Copilot SDK
+ Azure ML
+ localhost browser UI
```

Remote hosting, multi-user infrastructure, and production deployment are not required.

## 8. Clean Repository Decision

Build AI Research Intern as a clean repository from scratch.
Do not clone `github/awesome-copilot` as the application base.
Its Copilot SDK/Ralph examples remain references for session creation, working-directory setup, events, fresh iterations, and shutdown.
Borrow useful mechanics, not repository structure.
Our repository should directly reflect our research-controller architecture.

## 9. Copilot SDK Role

Use the Python `github-copilot-sdk`.
Copilot is the semantic reasoning and coding engine.
It may inspect code, metrics, logs, experiment history, and research state; formulate hypotheses; modify editable files; and run permitted local commands.
Copilot does not own global orchestration, budgets, permissions, Azure credentials, persistent state, or final scoring.
Those remain deterministic application responsibilities.

## 10. Session Model

Prefer a fresh Copilot session for each research iteration.
Each session receives objective, contract, current research state, selected parent, relevant metrics/history/logs, remaining budget, and current code.
Do not depend on one indefinitely growing Copilot conversation.
Research continuity comes from the ledger and research state.
Fresh sessions should be independently understandable and recoverable.

## 11. Experiment Repository and Contract

The experiment remains an ordinary researcher-owned ML repository.
A representative layout is:

```text
experiment/
├── train.py
├── model.py
├── dataset.py
├── evaluate.py
├── configs/
├── azure_job.yaml
└── .research_intern/
```

The contract must define objective, direction, optional target, execution configuration, expected outputs, editable scope, protected scope, constraints, and budget.
The controller validates it before research begins.

## 12. Protected Surfaces

The agent must not improve its score by changing the definition of success.
Protect the evaluator, objective, held-out/test data, constraints, budgets, contract policy, and researcher-declared protected paths.
Editable and protected scope must not overlap.
The controller verifies protected-state integrity before execution.
Copilot may only modify explicitly permitted surfaces.

## 13. Standard Outputs

Every Azure experiment must expose:

```text
experiment_outputs/
├── run.json
├── metrics.json
├── metrics_history.json
├── logs/
└── artifacts/
```

`run.json` records execution status and metadata.
`metrics.json` records final metrics; `metrics_history.json` records useful trajectories.
Core evaluation should use structured values rather than infer metrics from plots or prose.

## 14. Baseline and Experiment IDs

The existing human baseline becomes `EXP-000`.
The first autonomous candidate becomes `EXP-001`; later runs increment from there.
The baseline must follow the same result representation as later experiments.
It may be imported from an existing successful Azure run or executed by the system.
Bootstrap/integration work happens before `EXP-000` and is not itself an experiment.

## 15. Experiment Lifecycle

Every candidate follows:

```text
select parent
→ build research context
→ invoke Copilot
→ implement candidate
→ pre-flight validate
→ create Git commit
→ submit Azure job
→ collect outputs
→ evaluate
→ record experiment
→ rebuild research state
```

Reliability of this lifecycle is more important than sophisticated search.

## 16. Git and Ledger

Use one reusable experiment working directory for the serial MVP.
Each autonomous candidate receives a Git commit representing the exact code state that ran.
The ledger maps experiment ID ↔ parent ↔ commit ↔ Azure job ↔ result.
If a candidate regresses, keep its commit and record, then reset to the selected parent before the next branch.
Do not create a permanent worktree or full repository copy per experiment.

## 17. Experiment Memory

For every experiment record at least:

```text
experiment ID
parent
hypothesis
planned change
rationale
Git commit
code diff
Azure job ID
status
metrics
constraint results
decision
conclusion
```

Git history stores code state; the ledger stores scientific meaning.
Use SQLite plus filesystem storage; do not add a vector database or distributed datastore.

## 18. Research State

Do not send the complete experiment history to Copilot every iteration.
Build a compact research state from the ledger.
It should summarize objective, baseline, current best, last experiment, promising directions, rejected directions, open questions, and remaining budgets.
The ledger is the source of truth.
Research state is the working-memory handoff between fresh Copilot sessions.

## 19. Evaluation and Outcomes

The LLM does not decide whether its own experiment succeeded.
The deterministic evaluator checks run completion, output validity, constraints, improvement over parent, new-best status, and target completion.
Possible decisions are:

```text
KEEP
REJECT
FAILED
GOAL_REACHED
```

Regressions and failures remain in the ledger as useful evidence.
Execution failures may include exceptions, CUDA OOM, NaNs, missing outputs, or Azure failure.

## 20. Pre-Flight Validation

Before spending Azure GPU compute, run inexpensive checks:

```text
syntax validation
protected-file validation
contract validation
configuration validation
lightweight smoke test
budget check
```

The goal is to catch obviously invalid candidates cheaply.
Do not build a full static-analysis or sandbox platform for the hackathon.

## 21. Azure ML Boundary

Azure ML is the only required experiment execution backend.
The Azure adapter must submit jobs, capture job IDs, check status, detect terminal states, download outputs, and surface failures.
Azure execution is deterministic infrastructure, not another LLM agent.
The Copilot turn should end once a valid candidate is ready.
The controller then waits or polls while Azure runs.

## 22. LLM/GPU Separation and Budgets

Treat reasoning compute and experiment compute as separate systems:

```text
Copilot creates candidate
→ Copilot turn ends
→ Azure ML runs candidate
→ controller collects result
→ evaluator scores result
→ next Copilot session begins
```

Track experiment count and Azure compute usage; track Copilot/AI usage when practical.
The controller is the budget authority and the LLM cannot extend its own limits.
Stop on target reached, budget exhaustion, or explicit human stop.

## 23. Search and Agent Strategy

Do not begin with Bayesian optimization, evolutionary search, or a complex search controller.
Start with:

```text
research state
→ one Copilot-generated candidate
→ execute
→ evaluate
→ repeat
```

Keep the MVP single-agent.
Later, classical optimizers may tune numerical parameters while Copilot handles structural or semantic interventions.

## 24. Backend and UI

Use a local Python backend; FastAPI is appropriate for browser communication.
Research logic should live below thin route handlers.
The minimum UI should show objective, baseline, best score, budget, current experiment/status, experiment history, and selected experiment details.
Experiment detail should expose hypothesis, rationale, code diff, Azure job, metrics, decision, and conclusion.
VS Code remains the development environment; the browser acts as research mission control.

## 25. Activity, Authentication, and Credentials

Where practical, stream file reads, edits, validation, Git commits, Azure submission, waiting, collection, and evaluation to the UI.
Raw private chain-of-thought is not required; persist structured observation, diagnosis, hypothesis, candidate, expected effect, result, and conclusion.
The localhost dashboard provides Azure and Copilot browser sign-in controls.
Microsoft MSAL and the official Copilot CLI own provider authorization; there is
no hosted OAuth application or multi-user account system. Sign-in checks are
separate from approving service settings, measuring the baseline and starting research.
The backend owns Azure credentials and never places secrets inside Copilot prompts.
Expose narrow capabilities such as `submit_job()`, `get_job_status()`, and `collect_results()`.

## 26. Explicit Non-Goals

Do not build a universal autonomous scientist, full AutoML platform, literature-review system, paper-writing system, or autonomous research-question generator.
Do not build remote GitHub/GitLab integration, multi-user cloud hosting, multiple execution backends, parallel experiments, distributed orchestration, vector memory, or complex multi-agent systems.
Do not build adapters for every ML framework or logging ecosystem.
Do not introduce microservices, queues, distributed workers, enterprise RBAC, or production tenancy.
Do not create dedicated PyTorch, TensorFlow, JAX, YOLO, or Transformers platform layers.

## 27. Onboarding, Recovery, and Serial Execution

The browser accepts an existing project folder or ZIP without requiring a hand-written
contract or prescribed layout. Deterministic inspection finds Azure command YAML,
candidate evaluators, validation references and saved JSON metrics; the researcher
resolves ambiguity in labelled controls and states the goal, editable scope and limits.
The app generates its internal contract only after this review. It does not guess an
unfamiliar evaluator's arguments or invent scientific measurements.
Azure discovery uses the signed-in identity with exact manual IDs/names as fallback.
Existing jobs are explicitly selected, observed without Copilot, downloaded and
independently re-scored. Historical source association is user-attested and reported.
One-node command jobs with folder outputs are supported; pipelines/sweeps and universal
framework or logging adapters remain outside this implementation.
Persist enough state for completed history to survive restart: ledger records, Git commits, Azure job IDs, outputs, and current best.
Run one experiment at a time; no parallel scheduling or simultaneous worktrees are required.
Basic restart-aware recovery is desirable, while distributed fault tolerance is deferred.

## 28. Build Order

Build vertically in this order:

```text
1. Copilot SDK spike
2. one local candidate modification + Git commit
3. one complete Azure experiment + evaluation + ledger entry
4. autonomous repetition with research-state handoff
5. minimal browser UI
6. polish/recovery only after the loop works
```

Do not build broad infrastructure before one complete vertical experiment works.

## 29. Minimum Success

The backend can load and validate one prepared experiment, establish `EXP-000`, invoke Copilot for a permitted candidate, validate and version it, submit it to Azure ML, collect standardized results, evaluate them, persist the experiment, rebuild research state, and start the next iteration.
Later candidate choices must use accumulated experimental evidence rather than behave as stateless guesses.
At least one rejected or failed experiment should remain useful context.
The current best must be tracked separately from the latest attempt.
Normal iterations should require no human intervention between experiments.

## 30. Traceability and Final Output

For every experiment the system must answer:

```text
What was the parent?
What hypothesis was tested?
What changed?
What exact code ran?
Which Azure job ran it?
What metrics returned?
Were constraints satisfied?
Was it kept, rejected, or failed?
What conclusion was recorded?
```

At the end, expose the best experiment ID and exact Git state, baseline versus final metrics, constraint compliance, complete lineage, relevant Azure runs/artifacts, and a concise findings summary.

## 31. Demo Presentation Priority

The audience should be able to see:

```text
what the system observed
→ what it decided to try
→ what code changed
→ what Azure executed
→ what happened
→ what it learned
→ why it chose the next experiment
```

A small reliable loop with strong traceability is more valuable than broad but fragile feature coverage.

## 32. Scope-Cut Rule

If time becomes constrained, cut optional features in this order:

```text
UI polish
→ extra live-streaming features
→ advanced recovery
→ smarter search
→ optional final-audit enhancements
```

Do not cut external evaluation, experiment contract, protected scope, Git traceability, experiment ledger, research-state handoff, Azure execution, or bounded stopping conditions.

## 33. MVP Definition of Done

The baseline is registered as `EXP-000`.
Copilot autonomously proposes and implements later candidates.
Each candidate is validated and tied to an exact Git state.
Azure ML executes each valid candidate and returns the fixed result contract.
The deterministic evaluator records KEEP, REJECT, FAILED, or GOAL_REACHED.
The ledger and research state carry knowledge into the next fresh Copilot session.
The loop continues until the target or a declared budget boundary is reached.
The best experiment remains recoverable, reproducible, and meaningfully better than baseline under the declared constraints.
Anything not required to establish that proof is secondary.
