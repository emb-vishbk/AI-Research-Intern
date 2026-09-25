# AI Research Intern

## EXPERIMENT_CONTRACT.md

## 1. Purpose

This document defines the experiment contract between a researcher-owned ML experiment and the AI Research Intern.
The contract is the formal interface that allows the system to operate on different experiments without understanding every project-specific convention.
It defines what is being optimized, how the experiment runs, what the agent may change, what must remain protected, how results are returned, and when research must stop.
The contract is mandatory for autonomous execution.
If the contract is invalid, the research loop must not start.

## 2. Core Principle

The implemented live scorer interface and resource bindings are specified in
[Live scoring protocol](#live-scoring-protocol) at the end of this document.

The researcher should be free to structure the internal experiment code however they prefer.
The AI Research Intern should not require PyTorch-, TensorFlow-, Transformers-, YOLO-, or framework-specific project layouts.
Instead:

```text
arbitrary experiment implementation
+
small standardized interface
=
Research Intern compatible experiment
```

The contract standardizes the boundary, not the internal codebase.

## 3. Contract Responsibilities

The contract must answer:

```text
What are we optimizing?
How is the experiment executed?
What files may the agent change?
What files must remain protected?
What outputs must every experiment produce?
What constraints define a valid candidate?
What budgets bound the autonomous search?
What baseline does research start from?
```

These answers must be explicit enough for deterministic validation.

## 4. Human-Owned Decisions

The researcher must explicitly define the objective, search permissions, protected surfaces, hard constraints, and budgets.
Copilot must not invent these boundaries.
For example, Copilot must not independently decide that validation accuracy is the objective or that `evaluate.py` may be modified.
These choices define scientific validity and must remain human-controlled.
Copilot may reason about how to improve within these boundaries.

## 5. Recommended Location

Each compatible experiment repository should contain:

```text
experiment/
├── ...
└── .research_intern/
    └── contract.yaml
```

The contract should live inside the experiment repository so that configuration travels with the experiment.
The AI Research Intern application loads and validates this file when the project is initialized.
The exact filename may remain fixed as `.research_intern/contract.yaml` for the MVP.

## 6. Minimal Contract Shape

A representative contract is:

```yaml
version: "1.0"

objective:
  metric: validation_f1
  direction: maximize
  target: 0.87

execution:
  backend: azure_ml
  job_config: azure_job.yaml

outputs:
  root: experiment_outputs/
  run: run.json
  metrics: metrics.json
  history: metrics_history.json
  logs: logs/

scope:
  editable:
    - train.py
    - model.py
    - configs/
  protected:
    - evaluate.py
    - data/test/

constraints:
  latency_ms:
    max: 50

budget:
  max_experiments: 8
  max_gpu_hours: 6
```

Additional optional fields may be added later without changing the core abstraction.

## 7. Contract Version

Every contract must declare a schema version.
Example:

```yaml
version: "1.0"
```

The controller uses the version to determine which schema and validation behavior apply.
Unsupported versions must fail validation clearly.
Do not silently interpret unknown contract formats.

The offline core loader now accepts the fields defined in
`schemas/contract.schema.json` using strict, bounded YAML parsing. Duplicate keys,
aliases, unknown fields, non-finite numeric values, path traversal and scope overlap
are rejected. Directory scope paths end in `/`; other scope paths name exact files.
Paths are literal, not glob patterns, and scope comparisons conservatively ignore case.

## 8. Objective

The objective defines the primary value the autonomous loop should optimize.
Minimum fields:

```yaml
objective:
  metric: validation_f1
  direction: maximize
```

The metric name must correspond to a numeric value emitted by `metrics.json`.
Direction must be either `maximize` or `minimize`.
The evaluator, not Copilot, applies this direction.

### Optional fixed evaluation protocol

Version 1.0 additionally accepts an `evaluation` object. Legacy offline contracts
remain valid without it, but the real-project setup cannot mark evaluation confirmed
until it is supplied and explicitly reviewed by the researcher. No metric, split,
target or evaluation policy is inferred from an upload.

```yaml
evaluation:
  metric_definition: "Exact computation, aggregation, units and scale agreed by the researcher"
  procedure: "Reproducible evaluator invocation and measurement conditions; descriptive, never executed during setup"
  dataset_version: "Immutable researcher-supplied dataset version or content digest"
  evaluator: evaluate.py
  evaluator_sha256: "<64 lowercase hexadecimal characters>"
  validation_split: split_manifest.json
  validation_split_sha256: "<64 lowercase hexadecimal characters>"
```

This is a structural example, not an approved scientific protocol. For bounding-box
evaluation, the definition must resolve matters such as IoU thresholds, class
aggregation, detection matching and score scale. The researcher chooses these after
reviewing the task, existing evaluator and measured baseline. Keep constraints in
the existing top-level `constraints` map; document their measurement conditions in
`procedure`. Contradictory min/max bounds are rejected.

`evaluator` and `validation_split` name regular local files (up to 16 MiB each);
their hashes must match before the contract loads. Both paths become automatically
protected and cannot overlap editable scope or the output root. The split manifest
should identify immutable membership and reference annotations. Protect evaluator
helper modules, reference annotations and dataset manifests through `scope.protected`
as well. Dataset access, correctness of the metric calculation and the scientific
meaning of the supplied version remain researcher/workload responsibilities.

The contract's `evaluation_fingerprint` is SHA-256 over UTF-8 canonical JSON of
exactly `objective`, `constraints` and `evaluation` from `ExperimentContract.to_dict()`.
Use sorted keys, compact separators, ASCII escaping and finite JSON values. The
project API/UI exposes the computed value. A completed `run.json` must echo it
when the contract has an evaluation protocol; missing or mismatched fingerprints
fail output validation before scoring. The existing metric, direction, finite-value
and constraint checks still apply. A matching fingerprint is a provenance assertion,
not remote execution verification or a substitute for a protected scoring wrapper.

Local workspace preparation does not confirm the protocol automatically. The
researcher reviews the displayed policy and explicitly confirms it for the exact
source commit and contract digest. Keep setup unconfirmed while decisions are open.

## 9. Target

The objective may include an explicit stopping target:

```yaml
objective:
  metric: validation_f1
  direction: maximize
  target: 0.87
```

If the target is reached and all hard constraints are satisfied, the evaluator may return `GOAL_REACHED`.
A candidate may still be considered an improvement even if it does not reach the final target.
The target is therefore different from the parent-comparison rule.

## 10. Objective Examples

Maximize:

```yaml
objective:
  metric: mAP
  direction: maximize
  target: 0.63
```

Minimize:

```yaml
objective:
  metric: validation_loss
  direction: minimize
  target: 0.20
```

The evaluator should use one primary objective for the MVP.
Multi-objective optimization is deferred.

## 11. Search Boundary

The contract defines the allowed intervention space through editable paths.
Example:

```yaml
scope:
  editable:
    - train.py
    - model.py
    - configs/
    - augmentation/
```

Copilot may inspect the broader repository when necessary.
Modification authority applies only to explicitly editable paths.
Anything outside editable scope should be treated as read-only unless separately allowed.

## 12. Protected Scope

Protected paths represent surfaces the agent must never modify.
Example:

```yaml
scope:
  protected:
    - evaluate.py
    - data/test/
    - .research_intern/contract.yaml
```

Typical protected surfaces include evaluators, held-out test data, objective definitions, production deployment code, credentials, and safety-critical configuration.
Protected paths take precedence over editable declarations.
Editable and protected scopes must not overlap.

The current candidate workflow also protects `.research_intern/`, the referenced
Azure job configuration, generated outputs, and Git control files. Protected
declarations must exist when loading the prepared experiment; editable paths may
name future files/directories. Actual changes are checked independently of the
proposer's reported plan, including ignored/untracked files, deletions and renames.

## 13. Why Protection Matters

The optimizer must not change the definition of success.
Otherwise it could improve the reported score by modifying evaluation logic rather than improving the experiment.
The controller must verify actual filesystem changes after Copilot finishes.
Prompt instructions alone are not sufficient enforcement.
A protected-file modification must block Azure submission.

## 14. Read Access

For the MVP, Copilot may generally read the experiment repository unless the implementation declares additional restrictions.
Read access allows the agent to understand model architecture, data pipeline, training code, evaluator interfaces, configuration, and previous outputs.
Write access is much narrower.
This distinction should be preserved in the permission layer.

## 15. Execution Backend

The MVP supports:

```yaml
execution:
  backend: azure_ml
```

No generic cloud abstraction is required.
The purpose of the backend field is to make the contract explicit and future-compatible.
Unsupported execution backends should fail validation rather than trigger automatic adaptation.

## 16. Azure Job Configuration

The contract specifies the Azure ML job definition used to execute the candidate:

```yaml
execution:
  backend: azure_ml
  job_config: azure_job.yaml
```

The path must resolve inside the experiment repository.
The Research Intern does not expose Azure credentials to Copilot.
The deterministic Azure executor reads the configuration and submits the job.

## 17. Optional Execution Metadata

The contract may later include fields such as:

```yaml
execution:
  backend: azure_ml
  job_config: azure_job.yaml
  smoke_test: python -m pytest tests/smoke
```

For the MVP, optional metadata should only be added when required by the demo experiment.
Do not turn the contract into a full workflow-definition language.
Keep it small.

`execution.smoke_test` remains a future extension and is rejected by the current
offline schema. Preflight checks Python/JSON/YAML syntax without executing commands.

## 18. Standard Output Root

Every experiment must produce a known output root.
Example:

```yaml
outputs:
  root: experiment_outputs/
```

The Azure collector downloads or locates this directory after the job reaches a terminal state.
The structure inside it is governed by the output contract.
The evaluator should not search arbitrary directories for results.

## 19. Required Outputs

The MVP requires:

```text
experiment_outputs/
├── run.json
├── metrics.json
├── metrics_history.json
└── logs/
```

The MVP also requires an `artifacts/` directory, consistent with `MVP_SCOPE.md`
and the implemented collector. Both `logs/` and `artifacts/` may be empty.
Required outputs must be validated before evaluation.
Missing required outputs make the experiment invalid or failed.

## 20. `run.json`

`run.json` records execution-level information.
Example:

```json
{
  "schema_version": "1.0",
  "status": "completed",
  "experiment_id": "EXP-004",
  "parent_experiment": "EXP-003",
  "started_at": "...",
  "completed_at": "...",
  "parameters": {}
}
```

The status should distinguish successful completion from execution failure.

## 21. Failed `run.json`

A failed run may emit:

```json
{
  "schema_version": "1.0",
  "status": "failed",
  "failure_type": "training_error",
  "exit_code": 1
}
```

This allows the controller to distinguish failure diagnosis from model-quality evaluation.
If Azure fails before this file exists, Azure status and logs remain the authoritative execution evidence.

## 22. `metrics.json`

`metrics.json` contains final structured metrics.
Example:

```json
{
  "schema_version": "1.0",
  "primary_metric": {
    "name": "validation_f1",
    "value": 0.847,
    "direction": "maximize"
  },
  "metrics": {
    "validation_f1": 0.847,
    "precision": 0.86,
    "recall": 0.83,
    "latency_ms": 43
  }
}
```

The evaluator reads this file rather than extracting values from logs.

## 23. Primary Metric Consistency

The primary metric in `metrics.json` must match the objective declared in the contract.
For example:

```text
contract objective.metric = validation_f1
metrics.primary_metric.name = validation_f1
```

A mismatch must fail result validation.
The direction may also be checked for consistency.
The agent must not be allowed to substitute another metric after execution.

## 24. `metrics_history.json`

This file stores structured trajectory information.
Example:

```json
{
  "epoch": [1, 2, 3, 4],
  "train_loss": [1.2, 0.8, 0.5, 0.3],
  "val_loss": [1.3, 0.9, 0.86, 1.01],
  "validation_f1": [0.65, 0.75, 0.81, 0.80]
}
```

Copilot may use this evidence to diagnose training behavior.
Structured history is preferred over inferring curves from image plots.

## 25. Logs

The standard output should include:

```text
logs/
└── training.log
```

Logs are supporting diagnostic evidence.
They may help diagnose crashes, OOMs, NaNs, convergence problems, or implementation issues.
Logs should not be the primary source of objective metrics when structured files exist.

## 26. Artifacts

Optional artifacts may include:

```text
artifacts/
├── model/
├── checkpoints/
├── predictions/
└── plots/
```

Large artifacts remain on the filesystem rather than being stored directly in SQLite.
The ledger stores references and relevant metadata.
Artifact contents are not required for every research iteration unless the experiment needs them.

## 27. Constraints

Constraints define conditions that must remain satisfied while optimizing the objective.
Example:

```yaml
constraints:
  latency_ms:
    max: 50
  vram_gb:
    max: 24
```

Constraint metrics must appear in standardized outputs or be deterministically computable.
Hard constraint violations normally cause the candidate to be rejected.

## 28. Constraint Types

The initial schema may support:

```text
max
min
```

Example:

```yaml
constraints:
  accuracy:
    min: 0.80
  latency_ms:
    max: 50
```

Avoid complex logical constraint languages in the MVP.
Only implement operators required by the demo experiment.

## 29. Objective Versus Constraints

The objective determines what should improve.
Constraints determine what must remain acceptable.
Example:

```text
maximize validation_f1
subject to latency_ms <= 50
```

A candidate with higher F1 but unacceptable latency is not a valid improvement.
The evaluator must apply both.

## 30. Experiment Budget

The contract must bound autonomous search.
Example:

```yaml
budget:
  max_experiments: 8
```

The baseline `EXP-000` should normally not count as an autonomous experiment unless the implementation explicitly chooses otherwise.
The controller tracks executed autonomous trials.
Copilot cannot change this field.

The current offline implementation enforces a conservative reservation limit:
each candidate allocated an experiment ID consumes one `max_experiments` slot,
including later submission/runtime failures. Draft proposals and validation
failures before reservation do not consume slots. The limit is persisted before
baseline import and cannot change within that run. A value of zero permits
baseline setup but no candidates. Compute usage remains a separate measure.

Offline loop runs additionally persist a controller `max_attempts` limit for
candidate preparation, including failures before reservation. It is application
configuration, not an experiment-contract field or an Azure usage measure. Neither
the experiment limit nor the preparation limit can be replenished by resuming a run.

## 31. Azure Compute Budget

Where practical, include:

```yaml
budget:
  max_gpu_hours: 6
```

The controller should update actual or estimated usage after Azure runs.
A failed Azure job that consumed compute still contributes to compute usage.
Pre-flight failures do not consume Azure GPU budget.
The exact accounting implementation may be simplified for the demo.

## 32. Copilot / AI Budget

The contract may optionally include:

```yaml
budget:
  max_ai_credits: 5000
```

The controller should enforce a research-level cap when usage information is available.
This budget is separate from Azure compute.
The system should not assume that Copilot billing and Azure billing represent the same resource.

## 33. Budget Authority

Budget fields are protected research-policy inputs.
Copilot must not modify them.
The controller checks budget before beginning an iteration and again before expensive execution when appropriate.
Once a hard budget is exhausted, no new experiment should begin.
Budget enforcement must remain deterministic.

## 34. Baseline

The experiment must have a valid starting state.
The baseline may be defined through an existing Azure run reference or through locally available normalized outputs.
Conceptually:

```text
human experiment
→ successful baseline
→ normalize
→ EXP-000
```

The baseline uses the same metric and output representation as later experiments.

## 35. Baseline Requirements

`EXP-000` should include an exact Git code state, objective metric, constraint metrics, valid standardized outputs, and optional Azure job reference.
The baseline is the initial best experiment.
All subsequent candidates are evaluated relative to a parent and current best.
The autonomous loop must not begin without an evaluable baseline.

## 36. Git Requirement

For the MVP, the experiment workspace should be an independent Git repository.
Each executed experiment corresponds to an exact commit.
The contract does not need to describe Git internals.
The project loader verifies Git availability during initialization.
Git stores code history; the contract defines research semantics and permissions.

## 37. Contract Immutability During Research

Once an autonomous research run starts, the effective contract should be treated as immutable.
Copilot must not modify:

```text
objective
target
constraints
scope policy
budgets
output definitions
```

If the human changes the contract, the controller should treat that as a new or explicitly restarted research configuration.
Do not silently continue under changed success criteria.

## 38. Contract Validation

Before initializing `EXP-000`, validate:

```text
schema version supported
objective present
metric name valid
direction valid
execution backend supported
job config exists
output paths defined
editable paths valid
protected paths valid
no scope overlap
constraint format valid
budget valid
Git repository available
```

Validation errors should be explicit and actionable.

## 39. Output Validation

After each Azure run, validate:

```text
required files exist
JSON parses successfully
schema version supported
run status recognized
primary metric exists
primary metric is numeric
metric name matches objective
constraint metrics exist
```

Only then pass the result to the evaluator.
The output collector validates structure; the evaluator judges quality.

## 40. Pre-Flight Contract Enforcement

Before Azure submission, verify that actual candidate changes remain inside contract permissions.
Check the Git diff against editable and protected paths.
Validate relevant configuration and schemas.
Check remaining budgets.
If a candidate violates the contract, execution must be blocked.

## 41. Failure Categories

Contract-related failures should distinguish:

```text
CONTRACT_INVALID
PERMISSION_VIOLATION
OUTPUT_INVALID
EXECUTION_FAILED
EVALUATION_INVALID
```

A permission violation is different from a poor model result.
An invalid output is different from an Azure infrastructure failure.
These distinctions should remain visible in the ledger and UI.

## 42. Experiment Result Decisions

Once the output contract is valid, the evaluator may return:

```text
KEEP
REJECT
FAILED
GOAL_REACHED
```

`KEEP` means the candidate is a valid future parent.
`REJECT` means the run completed but should not become the active parent.
`FAILED` means meaningful objective evaluation could not be completed.
`GOAL_REACHED` means objective target and hard constraints are satisfied.

## 43. Example Complete Contract

```yaml
version: "1.0"

objective:
  metric: validation_f1
  direction: maximize
  target: 0.87

execution:
  backend: azure_ml
  job_config: azure_job.yaml

outputs:
  root: experiment_outputs/
  run: run.json
  metrics: metrics.json
  history: metrics_history.json
  logs: logs/
  artifacts: artifacts/

scope:
  editable:
    - train.py
    - model.py
    - configs/
    - augmentation/
  protected:
    - evaluate.py
    - data/test/
    - .research_intern/contract.yaml

constraints:
  latency_ms:
    max: 50
  vram_gb:
    max: 24

budget:
  max_experiments: 8
  max_gpu_hours: 6
  max_ai_credits: 5000
```

The MVP implementation may use a smaller subset where necessary.

## 44. Prepared Demo Experiment

The hackathon does not require perfect onboarding of arbitrary ML repositories.
The primary demo experiment may be deliberately prepared to satisfy this contract.
This lets engineering effort focus on the autonomous research loop rather than repository adaptation.
The experiment should still remain representative of ordinary researcher-written ML code.
Its contract should demonstrate the intended future integration boundary.

## 45. Future Bootstrap Phase

Later, Copilot may inspect a repository without `.research_intern/contract.yaml` and propose integration scaffolding.
It could infer training entrypoints, existing metrics, output locations, and Azure configuration.
It may create adapters that normalize future outputs into the standard contract.
However, objective, search permissions, protected surfaces, and budgets still require explicit human confirmation.
This bootstrap flow is not required for the first vertical MVP.

## 46. Generality Model

The Research Intern should depend on:

```text
contract
+
standard outputs
+
editable codebase
+
execution backend
```

It should not depend directly on the experiment using a particular ML library.
This abstraction is what later allows the same research controller to work across different ML projects.
Generality comes from a stable interface, not from supporting every framework explicitly.

## 47. Non-Goals

The experiment contract is not a full workflow engine.
It is not a replacement for Azure ML YAML.
It is not an AutoML search-space language.
It is not a dataset-management system.
It is not a general secrets-management format.
It is not intended to describe every possible scientific experiment.

## 48. Schema Implementation

The application should ship machine-readable schemas for important contract objects.
At minimum:

```text
schemas/
├── contract.schema.json
├── run.schema.json
└── metrics.schema.json
```

A schema for candidate plans may also exist.
The coding agent may use Pydantic models internally while keeping JSON/YAML formats stable.

The implemented loader uses PyYAML and immutable typed contract values. SQLite
persists the normalized effective contract and dedicated repository identity before
baseline import. Existing evaluation-only ledgers remain readable but cannot be
retrofitted with a full contract after baseline import. Use a new run for changed policy.
The output root and internal file/directory names are configurable end to end;
omitted names use the standard defaults. Azure configuration is parsed locally,
but service validation and actual GPU/AI budget accounting remain deferred.

## 49. Schema Evolution

Keep version `1.0` intentionally small.
New optional fields should preserve compatibility whenever practical.
Breaking structural changes require a new contract version.
Do not add fields speculatively.
Only expand the schema when a real experiment requires additional information.

## 50. Contract and Research State

The contract and research state serve different purposes.

```text
contract      = fixed research boundary
research state = evolving accumulated knowledge
```

The contract contains objective, permissions, execution rules, outputs, constraints, and budgets.
Research state contains baseline, best experiment, recent outcomes, supported/rejected directions, open questions, and remaining resources.
Never allow research-state conclusions to override contract policy.

## 51. Contract and Ledger

The contract describes what constitutes a valid research environment.
The ledger records what happened inside that environment.
Every experiment record should reference the effective contract version.
This makes later auditing possible.
The ledger must not become an alternative location for silently changing objective or constraints.

## 52. Contract and Copilot

Copilot receives the contract as context but does not own it.
The agent uses it to understand:

```text
what success means
what may change
what must remain protected
how much budget remains
what outputs the experiment must preserve
```

All critical contract rules must still be enforced deterministically outside the model.

## 53. Contract and Azure

The contract points to the experiment's Azure ML job configuration.
Azure executes the experiment code and produces standardized outputs.
The AI Research Intern should not duplicate the entire Azure job specification inside its own contract.
Keep execution-specific details in Azure-native configuration where possible.
The Research Intern contract should only declare the interface it needs.

## 54. Minimum Contract for First Implementation

The first implementation only needs:

```yaml
version: "1.0"

objective:
  metric: validation_f1
  direction: maximize
  target: 0.87

execution:
  backend: azure_ml
  job_config: azure_job.yaml

outputs:
  root: experiment_outputs/

scope:
  editable:
    - train.py
    - model.py
    - configs/
  protected:
    - evaluate.py
    - data/test/

budget:
  max_experiments: 8
```

Add constraints and compute budgets as soon as the demo experiment requires them.

## 55. Contract Invariants

1. The objective is human-defined.
2. The evaluator is outside Copilot's control.
3. Editable and protected scope never overlap.
4. Copilot cannot modify the effective contract during research.
5. Every executed experiment returns structured results.
6. Primary metric names match the declared objective.
7. Constraints are evaluated deterministically.
8. Budgets are enforced outside the LLM.
9. `EXP-000` and autonomous experiments use the same result contract.
10. Invalid contracts cannot enter the autonomous research loop.

## 56. Final Mental Model

The experiment contract is the handshake:

```text
Researcher's ML Experiment
        ↕
Experiment Contract
        ↕
AI Research Intern
```

The researcher keeps control over the scientific objective and boundaries.
The experiment keeps freedom over its internal implementation.
The Research Intern receives exactly enough structure to reason, modify, execute, evaluate, and repeat safely.
The MVP should preserve this boundary even when shortcuts are taken elsewhere.

## Live scoring protocol

Live setup requires the evaluation section, explicit source preparation and human
confirmation. Its fingerprint is the SHA-256 of an object containing the contract's
`objective`, `constraints` and `evaluation`, encoded as sorted, compact ASCII-escaped
JSON, as implemented by `ExperimentContract.evaluation_fingerprint`.
Both baseline and candidate `run.json` must include the matching fingerprint and
`source_commit`, in addition to the standard experiment/parent/status fields.
`parent_experiment` is JSON null for EXP-000; the command input uses the string
`none`, which the workload must convert when writing JSON.

The protected command YAML declares `type: command`, a reviewed command, exactly
the configured workload inputs plus `experiment_id`, `parent_experiment`,
`source_commit`, and a single `experiment_outputs` output. The adapter supplies
one compute instance, the approved environment and timeout. Other infrastructure
fields in this template are not applied. Approved live configuration binds scalar
inputs, versioned read-only Azure data assets or baseline/candidate source paths.

The baseline is a new job from the prepared source commit, without a coding turn.
Historical output imports cannot establish a live baseline. EXP-000 does not consume
an autonomous experiment slot, but does reserve compute time. A failed or
constraint-violating baseline prevents all candidate work.

Training still writes the standard `run.json`, `metrics.json`,
`metrics_history.json`, `logs/` and `artifacts/`. Self-reported numbers must satisfy
that format, but the live controller replaces them with independently computed
metrics for all scientific decisions.

The researcher supplies a protected Python evaluator and declares every local
helper/reference file it needs in `scoring.files`. The evaluator and split manifest
are mandatory members. Each file is at most 16 MiB, with a 64 MiB bundle ceiling.
Larger reference data must be accessible through the separately provisioned scoring
environment and its reviewed protocol. No candidate file is added to this bundle.

The controller invokes the frozen evaluator in a fresh local process:

```text
approved-python [isolated execution] evaluate.py
    --outputs /absolute/path/to/downloaded/experiment_outputs
    --request /absolute/path/to/request.json
    --result /absolute/path/to/score.json
```

Request fields are `experiment_id`, `parent_experiment`, `git_commit`, `job_id`,
`evaluation_fingerprint`, `objective` and `constraints`. The scorer must write:

```json
{
  "identity": {
    "experiment_id": "EXP-001",
    "parent_experiment": "EXP-000",
    "git_commit": "EXACT_SUBMITTED_40_HEX_COMMIT",
    "job_id": "EXACT_CONTROLLER_JOB_NAME",
    "evaluation_fingerprint": "EXACT_64_HEX_PROTOCOL_FINGERPRINT"
  },
  "metrics": {"YOUR_OBJECTIVE": 0.7, "YOUR_CONSTRAINT_METRIC": 12.0}
}
```

Identity must match the request exactly. Every objective and constraint metric
must be present and finite. The scorer must recompute from validated evidence or
independently evaluate the model; merely copying `metrics.json` is not sufficient.
Candidate code and unsafe serialized objects must not be executed by the scorer.

Frozen source is checked before and after scoring; candidate imports and ambient
credentials are excluded from the scorer process. Output-file hashes must remain
unchanged. A verified receipt stores identity, metrics, scorer hashes and evidence
hashes beside the outputs. Recovery accepts a cached receipt only if all these
anchors still match. Failure, wrong identity, missing metrics, timeout or changed
evidence prevents acceptance. This is process isolation, not an OS sandbox; the
researcher remains responsible for scorer correctness and its installed dependencies.

GPU and AI journals reserve upper allowances before service requests and retain
them after failure. Reservations are not actual usage or provider invoice caps.
