# Requirements for an AI Research Intern compatible ML experiment

Guide revision: 1.0 — 2026-09-14  
Experiment contract version: `"1.0"`  
Audience: researchers and coding agents building or adapting an independent ML repository

## 1. Read this first

Build a useful, independently runnable ML experiment. It may later be connected to
AI Research Intern for automated, bounded optimization of its code and configuration.
Using AI Research Intern must remain optional.

This guide defines the integration boundary. It does not choose the research task,
dataset, model, primary metric, numerical target, or compute budget. The researcher
supplies those decisions in a separate task brief. A coding agent may propose options
and implement the approved decisions, but must not silently invent the definition of
success or fabricate baseline measurements.

Give this file explicitly to the coding agent before implementation. If the repository
has an `AGENTS.md` or another agent instruction file, add a reference to this guide
without replacing unrelated project instructions. Merely copying this file into a
directory does not guarantee that an agent reads or follows it.

### Requirement labels

- **REQUIRED**: necessary for this repository's reliable experiment boundary or the
  current version 1.0 contract/export format. Some requirements need workload tests
  or human review; passing a schema alone does not verify all of them.
- **RECOMMENDED**: a practical implementation convention; equivalent arrangements
  are acceptable when documented.
- **PLANNED PLATFORM CONNECTION**: a platform capability or binding that is not yet
  available in the current offline implementation. Prepare for it without claiming
  that it already works.

### Current platform capability

As of this revision, the platform implements strict contract loading, controlled
candidate editing in dedicated fixture repositories, Git traceability, simulated
execution, metric comparison, SQLite history, research-state reconstruction,
bounded repetition, stopping, basic offline recovery, and a localhost API/browser
dashboard for those simulated runs.

The current `run` command creates synthetic fixtures and synthetic scores. It does
not import arbitrary research repositories, train models, invoke a live coding
adapter, or submit real Azure ML experiments. The dashboard can check a copy placed
at the platform's fixed `.runtime/research-project/repository/` location by parsing
its contract, declared paths and job YAML. That check does not import a measured
baseline or enable actual ML execution. Real project staging/baseline import, live
adapters, and compute/AI usage accounting remain integration work.

Consequently, distinguish these milestones:

1. The ML project works independently.
2. Its contract and exports satisfy the documented interface.
3. The platform can load and execute that project through a verified live integration.

This guide enables preparation for milestones 1 and 2. It does not certify milestone
3, guarantee that all checks pass, or guarantee that optimization will improve a model.

## 2. What the platform does

AI Research Intern is a controller for repeated, externally evaluated experiments:

```text
researcher supplies working experiment + objective + boundaries + baseline
    ↓
platform selects an eligible parent code state
    ↓
fresh coding-agent session proposes and implements one candidate
    ↓
platform verifies permissions and preflight checks
    ↓
Git records the exact candidate
    ↓
execution service runs one computational experiment
    ↓
trusted workload evaluator calculates metrics
    ↓
platform validates outputs and compares metrics with the objective and constraints
    ↓
ledger records evidence; compact research state is rebuilt
    ↓
continue from the current best or stop
```

The coding agent that builds this repository is distinct from the runtime coding
agent that may later modify permitted candidate files. During initial development,
the researcher can establish and review evaluation code and policy. Once a platform
run begins, those approved surfaces are fixed for that run.

| Owner | Responsibilities |
| --- | --- |
| Human researcher | Task definition, reference data, evaluation protocol, objective, allowed interventions, constraints, budgets, baseline approval. |
| This ML repository | Data loading, candidate implementation, training/transformation/inference, trusted scoring, reproducible configuration, dependency specification, output export. |
| AI Research Intern application | Parent selection, runtime coding sessions, permissions, Git operations, experiment IDs, submission/polling, result decisions, budgets, ledger, research memory, stopping and recovery. |
| Execution environment | Runs the committed workload with provisioned dependencies, data access and compute; exposes outputs. The target platform service is Azure ML. |

The workload must not create a competing platform ledger, control the outer research
loop, assign platform candidate IDs, select the next parent, or decide its own
`KEEP`/`REJECT`/`GOAL_REACHED` result.

## 3. Suitable research problems and required human decisions

**REQUIRED:** The task must support a repeatable computational trial, trustworthy
machine evaluation, a meaningful editable search space, and a finite resource budget.
Define one numeric primary objective with a maximize/minimize direction. Additional
metrics can be reported and used as fixed minimum/maximum constraints.

Suitable formulations include improving prediction quality, reducing prediction
error, improving a fine-tuning recipe, or reducing model size/latency while preserving
acceptable quality. These are possible uses of the interface, not built-in solvers.
Open-ended subjective quality without a defensible evaluator is insufficient.

Before declaring the experiment ready, record the researcher's decisions about:

| Decision | What must be explicit |
| --- | --- |
| Input and output | What one example contains and what prediction or transformed artifact must be produced. |
| Task semantics | Label meanings, units, class vocabulary or other target representation, including ambiguous/unknown cases. |
| Data | Approved source, immutable version, access method and train/validation/final-test separation. |
| Primary metric | Exact name, computation, aggregation, units/scale, direction, and optional target. |
| Constraints | Measured quantities, thresholds, and measurement conditions. |
| Search space | Which code/configuration can change and any project-specific allowed parameter ranges. |
| Protected surfaces | Evaluator, reference annotations, split definitions, policy, execution wrapper, and other fixed inputs. |
| Resources | Experiment allowance and applicable compute/AI limits. |
| Baseline | Exact code, configuration, environment, data/checkpoint lineage and measured results. |

Do not choose an arbitrary improvement target before measuring the baseline. If a
decision is missing, document it as unresolved and request that specific decision;
continue independent scaffolding and tests where possible. Unapproved placeholders
must not become an active research configuration.

## 4. Standalone operation is required

**REQUIRED:** A researcher must be able to install dependencies, run a trial, produce
predictions/artifacts, evaluate them, inspect results, and run tests without installing
AI Research Intern or signing into Copilot or Azure.

Implement the workload using its normal ML libraries. Do not require:

- Imports from `research_intern` or inheritance from a platform base class.
- A running platform API, browser, agent session, or remote callback.
- The platform's SQLite database, private paths, or internal Python objects.
- Azure/Copilot credentials just to import the package, inspect configuration, or
  run local fixture tests.

Provide ordinary CLI entry points or equivalent scripts. Notebooks may supplement
the project, but the trial must run without notebook execution order, IDE state,
interactive prompts, or hidden globals. Resolve paths explicitly; avoid hard-coded
developer drive letters or usernames.

Dependency installation and acquisition of approved data/model assets are explicit
setup steps. Do not download assets as an import side effect or silently substitute
a different dataset, model, or synthetic score when assets are missing. Document
which operations need network access or access rights. Tests use tiny local fixtures
by default and do not spend cloud or model-service resources.

## 5. Repository organization

Only the contract location and declared interface paths are fixed. Internal source
layout and framework choice are flexible. The following is an **illustrative layout**;
rename paths consistently in commands and the contract if using another structure.

```text
research-project/
├── RESEARCH_INTERN_REQUIREMENTS.md       this portable guide
├── README.md                            installation, task, commands, measured status
├── pyproject.toml                       package and dependency declarations
├── requirements.lock                    chosen reproducible dependency specification
├── .gitignore
├── .research_intern/
│   └── contract.yaml                    required platform contract location
├── execution/
│   └── azure_job.yaml                   declared future cloud execution boundary
├── configs/
│   ├── candidate.yaml                   allowed research knobs
│   └── evaluation.yaml                  fixed metric/data/measurement rules
├── data_manifest.json                   immutable data references and fingerprints
├── split_manifest.json                  fixed membership or reproducible split definition
├── src/
│   └── experiment/
│       ├── __init__.py
│       ├── candidate/                   editable workload implementation
│       ├── evaluation/                  trusted scoring and reference-data handling
│       └── integration/                 trusted CLI, identity and output export
└── tests/
    ├── test_candidate.py
    └── evaluation/                      protected scoring/contract checks
```

`requirements.lock` is an example filename; use the lock mechanism appropriate to
the selected dependency tool. Protect the actual files that fix dependencies and
evaluation behavior. Existing repositories can add a small integration layer rather
than reorganize all source code.

Keep large datasets, checkpoints, caches, virtual environments, and trial outputs
outside the dedicated candidate source checkout. Track small manifests and source
configuration in Git. A model checkpoint is an artifact with its own identity and
checksum; a Git commit alone does not identify its learned weights.

## 6. One trial and its connection points

**REQUIRED:** Expose a noninteractive operation that executes exactly one trial from
explicit inputs and produces isolated outputs. The operation may train, fine-tune,
quantize, prune, or perform another approved intervention; a training stage is not
mandatory for every task. Include prediction/transformation validation and trusted
evaluation appropriate to the task.

| Connection point | Repository supplies | Platform behavior or status |
| --- | --- | --- |
| Contract | `.research_intern/contract.yaml` | The current loader validates the supported version and fields. |
| Candidate code | Declared editable files and readable documentation | A future live coding adapter uses the prepared parent and allowed scope. |
| Execution definition | YAML file named by `execution.job_config` | Currently parsed as a local mapping; actual Azure submission remains planned. |
| Trial inputs | Documented config, data/artifact paths, output path and identity inputs | The future executor must bind its request to these explicitly. |
| Result export | `run.json`, final metrics, metric history, logs and artifacts | The existing collector validates the declared output layout and identities. |
| Baseline | Measured compatible outputs, exact source commit, data/environment/artifact references | Existing import services support validation/scoring; external-project onboarding is still planned. |
| Activity | Normal execution logs and optional numeric metric history | The platform owns lifecycle events and future browser updates. |

No webhook, HTTP callback, SDK plugin, or live platform connection is required inside
the workload. Process invocation, declared configuration, and files are sufficient.
The platform's internal Python interfaces are not dependencies of this repository.

### Recommended CLI convention

The following is a **proposed project convention to implement**, not an existing
platform CLI command. Adapt the module name to the project's structure and document
the equivalent binding if different.

```bash
# Ordinary independent trial; paths are supplied by the researcher.
python -m experiment.integration.run \
  --config configs/candidate.yaml \
  --data-root /path/to/approved/data \
  --output-dir /path/to/new/run

# Export an agreed baseline identity for later platform import.
python -m experiment.integration.run \
  --config configs/candidate.yaml \
  --data-root /path/to/approved/data \
  --output-dir /path/to/new/baseline \
  --experiment-id EXP-000 \
  --parent-experiment none

# Illustrative platform-bound trial; actual IDs come from its job request.
python -m experiment.integration.run \
  --config configs/candidate.yaml \
  --data-root /path/to/approved/data \
  --output-dir /path/to/new/candidate \
  --experiment-id EXP-001 \
  --parent-experiment EXP-000
```

Define `none` as an explicit CLI sentinel that serializes to JSON `null` for the
baseline. For a platform candidate, require the supplied parent ID. The adapter
must not guess IDs from directory names, Git messages, timestamps, or previous runs.
There is no implemented platform environment-variable convention for injecting
these values. Additional non-secret provenance may be supplied as explicit inputs.

Standalone runs may use their own local identifiers in extra metadata. Without
agreed platform experiment/parent IDs, their completed `run.json` is not yet an
importable platform export. Provide a deliberate export step, or rerun with agreed
identity inputs; preserve original provenance and never relabel a different trial
as an existing platform experiment. Standalone execution itself must not require
the platform contract to be loaded.

The output directory must be isolated per trial. Refuse an existing completed result
unless a separately documented checkpoint-resume procedure applies. Cloud output
mounts may already be empty directories, so distinguish an empty provisioned output
directory from an existing result. Never overwrite a prior trial's evidence.

## 7. The supported experiment contract

**REQUIRED:** Create `.research_intern/contract.yaml` using the exact supported field
names. The example below is structurally valid for version 1.0. Its paths must exist
with the described types when loaded. `validation_score` is a placeholder metric
name to replace with the researcher-approved metric.

The example intentionally permits **zero autonomous experiments** until a human
sets the actual budget. No target is specified until a measured target is agreed.

```yaml
version: "1.0"

objective:
  metric: validation_score
  direction: maximize

execution:
  backend: azure_ml
  job_config: execution/azure_job.yaml

outputs:
  root: experiment_outputs/
  run: run.json
  metrics: metrics.json
  history: metrics_history.json
  logs: logs/
  artifacts: artifacts/

scope:
  editable:
    - src/experiment/candidate/
    - configs/candidate.yaml
  protected:
    - src/experiment/evaluation/
    - src/experiment/integration/
    - configs/evaluation.yaml
    - data_manifest.json
    - split_manifest.json
    - pyproject.toml
    - requirements.lock
    - tests/evaluation/

constraints: {}

budget:
  max_experiments: 0
```

### Field rules

| Field | Version 1.0 meaning |
| --- | --- |
| `version` | The string `"1.0"`; quote it in YAML. |
| `objective.metric` | One exact, trimmed, nonempty name of up to 128 characters, without control characters. |
| `objective.direction` | Exactly `maximize` or `minimize`. |
| `objective.target` | Optional finite number on the metric's declared scale; omit when unknown. Do not use `null`. |
| `execution.backend` | Exactly `azure_ml`. `simulated` and `local` are not valid contract backends. Local standalone commands remain available independently. |
| `execution.job_config` | Repository-relative path to an existing YAML object. Parsing it does not validate Azure resources. |
| `outputs.root` | Logical result directory. Other output names are relative to this root. |
| `scope.editable` | Nonempty list of explicitly allowed file/directory paths. |
| `scope.protected` | List of protected existing files/directories; may be empty structurally, but actual scientific protection is still required. |
| `constraints` | Optional mapping of metric names to numeric `min`, `max`, or both. A minimum must not exceed its maximum. |
| `budget.max_experiments` | Required nonnegative integer, fixed before baseline import. |
| `budget.max_gpu_hours` | Optional finite nonnegative number; declaration is supported, actual accounting is not implemented yet. |
| `budget.max_ai_credits` | Optional finite nonnegative number; declaration is supported, actual accounting is not implemented yet. |

An example constraint fragment, **only if latency is relevant and measured**, is:

```yaml
constraints:
  latency_ms:
    max: 50
```

Every constraint metric must appear in the final metrics object. Its protocol must
define hardware, batch size, warmup, repetitions, aggregation and units where
applicable. Do not interpret a workload-reported runtime as authoritative cloud
billing or measured GPU-hours.

The current baseline importer requires the baseline to satisfy hard constraints.
If the starting model violates a desired size/latency limit, do not quietly falsify
the baseline or relax approved rules. Agree on a compatible formulation, such as
minimizing that quantity toward a target while constraining quality, or explicitly
change the platform evaluation policy in a future implementation.

### Strict syntax and path rules

- Use a single YAML document with unique string keys. Aliases/merges and custom
  YAML tags are unsupported; do not rely on them.
- Contract and Azure job YAML inputs are limited to 256 KiB each in the current loader.
- Unknown contract fields are rejected. Do not add `task`, `framework`, `dataset`,
  `entrypoint`, `smoke_test`, credentials, or an optimization-space language to this
  schema. Keep additional project details in normal project files.
- Use literal relative paths with `/` separators. Directory scope entries end in
  `/`; file entries do not. Paths must be distinct from protected/output surfaces.
- Do not use absolute paths, drive letters, `..`, `.` components, backslashes,
  wildcards, empty components, trailing dots/spaces, or `.git` components.
- Scope comparisons are conservatively case-insensitive. Avoid case-colliding paths
  and duplicate scope entries. Existing paths must match their declared file/directory type.
- Output paths must be distinct and non-overlapping. Do not place inputs or protected
  policy under the output root.

The platform automatically protects `.research_intern/`, the declared job YAML and
the declared output root against candidate editing. Git metadata/control files are
also outside the editable boundary. Protection of outputs applies during candidate
preparation; the trusted execution/evaluation stage must still create fresh outputs.

## 8. Separate candidate code from scientific authority

**REQUIRED:** Changes to the candidate must not change what success means.

Protect the complete evaluation dependency chain: metric implementation, label
mapping, reference annotations, split membership, aggregation rules, thresholds,
measurement configuration, dependency versions and tests that establish correctness.
Protect the trial wrapper and output writer where they establish identity or publish
authoritative results. Avoid a protected `evaluate.py` that delegates scoring to an
editable helper or imports an editable threshold configuration.

Document an explicit prediction/artifact interface between the candidate and the
trusted evaluator. The evaluator computes metrics from actual outputs and trusted
reference data; it must not accept a score supplied by the candidate as evidence.
Validate missing/duplicate sample IDs, malformed predictions, invalid labels,
non-finite values and task-specific shape/range constraints. Do not silently drop
difficult samples or change the evaluation denominator.

Read-only paths alone do not prevent information leakage or arbitrary code effects.
Keep final-test examples and labels out of the runtime editing agent's context and
candidate-development workspace. Use validation evidence for iteration and reserve
final-test evaluation for the human-approved audit protocol. The platform's current
filesystem checks are not an operating-system sandbox or an enforced separation
between hostile training and scoring processes.

For the prepared workload, keep trusted scoring structurally separate from editable
candidate code and document any execution-isolation assumptions. Live execution must
eventually enforce access boundaries; copying this guide does not enforce them.

Both the human-approved contract and evaluator remain fixed throughout one research
run. Changing the objective, data split, metric semantics, protected policy or budget
requires a new explicitly approved research configuration. The runtime agent cannot
authorize that change itself.

## 9. Data, dependencies and reproducibility

**REQUIRED:** Each trial must have enough recorded information to reproduce the
comparison, even when exact numerical determinism is unavailable.

Record:

- Exact source commit and effective candidate configuration.
- Dataset version/fingerprint, split definition and label/target specification.
- Initial checkpoint identity and checksum where applicable.
- Seeds and the deterministic/non-deterministic settings used.
- Framework/dependency versions and execution environment identity.
- Hardware and measurement configuration when they affect the objective/constraints.
- Final model/artifact identities and references to logs, predictions and metrics.

Place nonnumeric provenance in `run.json` metadata or an artifact such as
`artifacts/provenance.json`. Keep the `metrics` mapping strictly numeric.

Use fixed, appropriate data separation, including temporal/group/entity separation
when required by the task. Do not regenerate a random split on each candidate or
select a seed based on the most favorable score. Large reference data may live
outside Git, but its immutable identity and evaluator access rules must be recorded.

Do not assume that selecting a parent Git commit also restores its trained weights.
For the first workload, define whether every candidate starts from the same fixed
checkpoint or intentionally warm-starts from a recorded parent artifact. A warm-start
policy needs explicit artifact binding; automatic parent-checkpoint injection is
not implemented in the current platform.

**RECOMMENDED:** Repeat promising stochastic trials under a fixed seed protocol and
report mean, variability and per-seed evidence. The current platform compares one
reported scalar and does not perform significance testing or automatic multi-seed
aggregation. If aggregation is part of the objective, implement it in trusted
evaluation, account for all computation, and freeze that policy before comparison.
Do not claim a reliable improvement solely because one noisy result is slightly higher.

## 10. Standard outputs

**REQUIRED for a completed platform export:** Produce these files and directories
under the supplied output destination, using the names declared by the contract:

```text
experiment_outputs/
├── run.json
├── metrics.json
├── metrics_history.json
├── logs/
└── artifacts/
```

The default names above should be preferred. The contract permits custom names;
the workload and future output-collection binding must agree on them. Both `logs/`
and `artifacts/` must exist, although the collector permits them to be empty.

The destination passed by a local caller or cloud job may have a different physical
path from `outputs.root`. Write the files directly into the supplied destination;
do not append another `experiment_outputs/` below it. The executor is responsible
for collecting that destination into the platform's per-experiment storage.

Use regular files/directories, UTF-8 JSON objects, unique JSON keys and finite
numbers. The current collector permits at most 4 MiB per parsed JSON file. Store
large predictions, per-example metrics, plots and checkpoints in artifacts rather
than bloating the final metrics/history JSON. Do not depend on symlinked outputs.

The following numbers and timestamps demonstrate format only. The actual writer
must use measured results and real identity; never copy these example scores into
a real experiment or substitute them after a failed computation.

### 10.1 Completed `run.json`

```json
{
  "schema_version": "1.0",
  "status": "completed",
  "experiment_id": "EXP-001",
  "parent_experiment": "EXP-000",
  "exit_code": 0,
  "started_at": "2026-09-14T10:00:00Z",
  "completed_at": "2026-09-14T10:01:00Z",
  "parameters": {
    "seed": 123,
    "candidate_config": "configs/candidate.yaml"
  },
  "provenance_artifact": "artifacts/provenance.json"
}
```

The collector requires `schema_version`, `status`, and matching experiment/parent
identity for completion. If `exit_code` is present, it must be integer zero.
Timestamps, parameters and provenance are useful extra metadata; the current
collector does not validate their scientific correctness. If metadata names an
artifact, create that real artifact.

Use `EXP-000` and JSON `null` as its parent for a baseline export. Candidate IDs
match `EXP-` followed by at least three digits, such as `EXP-001` or `EXP-1000`.
IDs are unique within their platform run. Receive them from that run's request;
never allocate or reuse them inside the trial runner.

### 10.2 Completed `metrics.json`

```json
{
  "schema_version": "1.0",
  "primary_metric": {
    "name": "validation_score",
    "direction": "maximize",
    "value": 0.81
  },
  "metrics": {
    "validation_score": 0.81,
    "trial_wall_time_s": 60.0,
    "peak_memory_mb": 256.0
  }
}
```

- The primary name and direction must exactly match the effective contract.
- The primary value must equal `metrics[objective.metric]`.
- Include every constraint metric, whether or not the constraint passed.
- Every metrics value must be a finite number. Strings, booleans, `null`, NaN and
  Infinity are invalid. Keep units in documentation/names, not numeric strings.
- Report measured values without clipping them to targets, replacing errors with
  zero, or presenting an incomplete evaluation as completion.
- Additional numeric metrics are allowed; they do not become objectives automatically.

### 10.3 `metrics_history.json`

```json
{
  "step": [1, 2, 3],
  "validation_score": [0.72, 0.78, 0.81]
}
```

This must be an object whose values are arrays of finite numbers. Do not use nested
epoch objects or arrays of records for this particular file. An empty object `{}`
is accepted when a one-shot transformation has no metric trajectory. Do not invent
history for such a task. Document alignment if multiple series are intended to share
the same steps; the current collector checks numeric structure, not series semantics.

The final metric must come from the predeclared evaluation/checkpoint-selection
protocol. Do not cherry-pick the most favorable reported history value unless that
selection rule was explicitly established in advance.

### 10.4 Failed `run.json`

```json
{
  "schema_version": "1.0",
  "status": "failed",
  "experiment_id": "EXP-001",
  "parent_experiment": "EXP-000",
  "exit_code": 1,
  "failure_type": "training_error",
  "message": "Trial execution failed; see logs/error.log."
}
```

On failure, exit nonzero, preserve diagnostics and write a failure record when the
process can do so. Failed runs need not fabricate final metrics or complete history.
The collector allows a failed run to omit identity, but any included identity must
match the request; supplying it is strongly recommended. A hard process kill may
prevent any failure file from being written, so the executor also relies on job state.

A completed trial with poor quality or violated constraints is still a completed
execution with honest metrics. The platform decides `REJECT`. A crash or invalid
output is an execution/output failure. Do not turn poor quality into a training error
or turn a crash into a valid low score.

### 10.5 Publishing and retaining evidence

Write each structured file through a temporary file and atomic replacement where
the filesystem supports it. Publish a completed `run.json` only after required
outputs have been written successfully. Do not leave a completed marker from an
earlier attempt in a reused directory. Where feasible, use a distinct attempt
directory and publish the complete result together.

Keep failed/rejected evidence. Do not delete it to make history appear successful.
Do not write into another trial's output directory. Ordinary logs should include
the supplied experiment ID when available, useful errors and concise progress;
raw private chain-of-thought is not an output requirement.

## 11. Baseline and comparison semantics

**REQUIRED:** Supply a real, reviewed baseline before autonomous research begins.

1. Finalize the data split, evaluator and objective with the researcher.
2. Verify the baseline's dependencies and data/checkpoint provenance.
3. Commit the exact source/configuration intended for baseline measurement.
4. Execute it and export measured results using the same protocol as later candidates.
5. Confirm output validity and hard-constraint eligibility.
6. Record the source SHA, outputs and artifact references; agree on any numerical target.

If finalizing the contract creates a later commit, retain the exact measured source
SHA and transparently establish that the import commit reproduces the same workload.
Rerun when needed. Never attach a score to an unrelated or subsequently modified
code state merely to satisfy an import check.

`EXP-000` denotes the baseline. Setup work is not an autonomous experiment. The
platform allocates `EXP-001`, `EXP-002`, and later IDs only through its own ledger.
One reserved candidate consumes an experiment slot even if submission or execution
later fails. A pre-reservation validation failure does not consume an experiment
slot; the offline platform separately caps preparation attempts. Neither this cap
nor GPU/AI credentials belongs in the workload contract beyond its supported fields.

The latest experiment may be a rejection or failure while an older experiment
remains best. Do not assume that a file named `latest` identifies the selected
parent or best checkpoint. The platform provides the parent code state explicitly.

The current evaluator supports a scalar objective, fixed min/max constraints and
an optional target. It does not implement a multi-objective Pareto search. Constraint
violations reject a candidate; target satisfaction requires all constraints to pass.
Baseline eligibility is checked as well. Preserve both successful and negative results.

## 12. Azure execution boundary — prepare now, connect later

**PLANNED PLATFORM CONNECTION:** The contract names an Azure ML command-job YAML.
The workload remains a normal command-line program. Azure credentials, submission,
job IDs and polling belong to the platform/operator.

A task brief may require a real standalone Azure baseline before handoff. In that
case, a small human-triggered operator helper may submit one job, persist its identity,
poll and resume output collection. Keep it separate from training/evaluation and
protect it from candidate edits. This helper does not implement the platform's
autonomous controller or prove live platform integration. Verify the actual cloud
run and downloaded outputs when the task requires them; a YAML template is insufficient.

Azure command jobs support versioned environment references, data inputs, named
outputs, and command bindings such as `${{inputs.dataset}}` and
`${{outputs.experiment_outputs}}`. Use an environment that already contains the
project's required runtime dependencies. [Azure command-job reference](https://learn.microsoft.com/en-us/azure/machine-learning/reference-yaml-job-command?view=azureml-api-2).

The following is an **illustrative job template**, with deliberate placeholders.
It is not provisioned or verified against an Azure workspace. Resolve and validate
the resources later; do not submit it automatically during repository scaffolding.

```yaml
type: command
code: ..
environment: azureml:REPLACE_WITH_ENVIRONMENT:1
compute: azureml:REPLACE_WITH_COMPUTE
inputs:
  dataset:
    type: uri_folder
    path: azureml:REPLACE_WITH_DATA_ASSET:1
    mode: ro_mount
  experiment_id: REPLACE_WITH_ASSIGNED_ID
  parent_experiment: REPLACE_WITH_ASSIGNED_PARENT_OR_NONE
outputs:
  experiment_outputs:
    type: uri_folder
    mode: rw_mount
command: >-
  python -B -m experiment.integration.run
  --config configs/candidate.yaml
  --data-root "${{inputs.dataset}}"
  --output-dir "${{outputs.experiment_outputs}}"
  --experiment-id "${{inputs.experiment_id}}"
  --parent-experiment "${{inputs.parent_experiment}}"
environment_variables:
  PYTHONPATH: src
```

For a file under `execution/`, `code: ..` is intended to select the repository root;
verify path resolution in the eventual submission code. Input/output names here
are conventions proposed by this guide, not bindings already implemented by the
platform. The future executor must supply actual identities and collect the named
output into the matching experiment. [Azure job input/output syntax](https://learn.microsoft.com/en-us/azure/machine-learning/reference-yaml-job-command?view=azureml-api-2).

The source submitted must match the recorded candidate commit. Stage only approved
source and small manifests; avoid uploading local datasets, secrets or generated
runs as part of the code snapshot. Provide separate inputs for required checkpoints
and data assets when applicable. Do not expose final-test data to the editing agent.

No real compute/environment/data resource names can be inferred from this guide.
Authentication and resource setup are separate integration steps. Passing the
platform's current local YAML parser proves only that this file is a YAML mapping.
It does not prove that Azure accepts it, its assets exist, or a job can execute.

## 13. Git and workspace compatibility

**REQUIRED:** Keep source, approved evaluation policy, manifests and the contract
versioned. Before a trial, its code must map to an exact commit. Candidate execution
must not mutate source files, tracked configuration, Git state, the contract, or
protected reference data. Keep runtime writes in explicitly supplied output/cache
locations.

The researcher can develop in an ordinary independent Git repository. The future
platform loader must prepare a dedicated controlled workspace; it must not reset
the researcher's active VS Code checkout. Do not move the project into the platform
or delete its remotes/configuration just to imitate the current test fixtures.

Current offline fixture restrictions are narrower than general repository support:

- Dedicated independent `.git` directory inside a platform run under `.runtime/`.
- Ordinary SHA-1 Git objects and only permitted basic core configuration.
- No linked worktrees, submodules, shallow/shared-object history, Git attributes,
  symlinks, hardlinks or special files.
- A clean, known committed source tree. Ignored and untracked files are inventoried
  too; `.gitignore` does not make local caches safe to leave in that checkout.
- No unversionable empty directories. Protected source directories should contain
  tracked files, not only empty placeholders.
- At most 16 MiB per inspected source file, 128 MiB per scanned tree, and 10,000
  entries. This is not a large-model/data storage implementation.

These are current staging limitations, not a requirement to give up normal source
repository workflows. A compatible external-project staging path remains to be
implemented and validated. Keep large assets separate now to simplify that work.
Do not assume Git LFS or embedded virtual environments work in the current fixture path.

The platform's current preflight checks Python syntax, JSON objects and YAML syntax
without executing researcher code. It does not run the project's smoke tests or
prove dependency/model correctness. Its JSON preflight expects an object at the
top level, so editable `.json` configuration files should use objects rather than
top-level arrays. Document richer project validation separately.

## 14. Required tests and acceptance evidence

Build tests that establish the actual workload boundary. Do not make all tests
depend on a full dataset, a pretrained-model download, a GPU, Azure or Copilot.

| Check | Required evidence |
| --- | --- |
| Candidate execution | A tiny local fixture completes the real code path and produces a valid prediction/artifact. |
| Trusted scoring | Known correct, incorrect and malformed predictions produce expected scores or explicit errors. |
| Sample/data integrity | Missing/duplicate IDs, split leakage and invalid target representations are detected where applicable. |
| Output export | Completed baseline/candidate exports have exact identity and objective/constraint metrics; required directories exist. |
| Failure behavior | Controlled training/evaluation errors exit nonzero and preserve failure diagnostics without fabricated successful scores. |
| Independence | The local workflow works without AI Research Intern imports, an active API, cloud credentials or a coding-agent session. |
| Source integrity | Running a trial does not modify protected source/policy/reference inputs or alter Git state. |
| Repeatability | Seeds, effective configuration and provenance are recorded; stochastic variation is measured under the approved protocol. |

Include negative export checks for mismatched identities, missing metrics, wrong
metric direction, conflicting primary values, duplicate JSON keys and non-finite
numbers. A schema check alone is insufficient for these semantic requirements.

Use separately marked opt-in tests for real data, expensive training and cloud
execution. Report which tests ran and which requirements remain unverified. A tiny
fixture smoke test proves integration behavior, not production task quality.

The platform does not yet expose a general external-project `validate-project`
command. Do not document a fictional working invocation. A project-local test/export
validator is acceptable, but keep it aligned with this version's contract/output
rules; platform acceptance must eventually use the platform's own checks.

## 15. Instructions for the coding agent building this repository

Follow this sequence, adapting it to an existing repository if one is already present:

1. Read this guide and the researcher's task brief. Inspect existing source and
   preserve unrelated work. Identify unresolved scientific decisions and asset access.
2. Establish the smallest independently runnable workload and reproducible environment.
   Keep model, dataset, metric and framework choices task-specific.
3. Separate editable candidate implementation from trusted evaluation, data policy,
   identity handling and result export. Implement known-answer evaluator tests early.
4. Implement explicit configuration/data/output inputs and one complete local fixture
   run with genuine computation. Test failure paths as well as completion.
5. Add the version 1.0 contract using real paths and human-approved decisions. Keep
   autonomous budget at zero until its value is approved. Do not add unsupported fields.
6. Implement compatible output export and provenance. Clearly distinguish standalone
   identifiers, synthetic test evidence and measured baseline/candidate evidence.
7. Prepare the Azure YAML boundary without inventing resources or initiating sign-in,
   downloads or cloud jobs beyond what the researcher has authorized.
8. Once data/model assets and execution are available, measure and record the baseline
   and agree on the target. Do not report this step complete based on canned values.
9. Update the project README with exact setup, trial, inference/transformation,
   evaluation and test commands; list remaining integration limitations.
10. Deliver a concise readiness report: what runs independently, which tests passed,
    where measured evidence lives, whether contract exports validate, and what still
    blocks real platform execution.

Do not implement the AI Research Intern controller, Copilot adapter, platform Azure
orchestration, platform UI, permanent research ledger, or autonomous repetition inside
this repository. A task-requested one-job operator helper is permitted as described
in section 12. Keep the public experiment boundary small enough for a human to use directly.

## 16. Definition of a prepared handoff

Before claiming this project is ready for platform integration, provide:

- A standalone runnable experiment and documented environment setup.
- An approved task definition, trusted evaluator, fixed data/split protocol and tests.
- A valid `.research_intern/contract.yaml` with real paths and approved policy.
- A declared one-trial invocation and documented identity/output binding.
- Measured baseline outputs tied to exact code, data, environment and model artifacts.
- Honest standardized success/failure exports and retained diagnostics.
- The Azure execution template and an explicit list of any unprovisioned resources.
- A clean source handoff, separate from large assets and generated runs.
- Evidence distinguishing completed local checks from untested live integrations.

Do not claim automated ML improvement until real candidate experiments have produced
trustworthy measurements under the agreed protocol. The project remains valuable
and usable independently even if AI Research Intern is never connected.

## 17. Version alignment and maintenance

This is a portable onboarding guide derived from the platform's existing experiment
contract and current validators. It is not an additional architecture specification.
Keep its copy versioned in the researcher repository so changes are reviewable.

In the platform source distribution, the relevant authorities are
`Docs/EXPERIMENT_CONTRACT.md`, `schemas/contract.schema.json`, `schemas/run.schema.json`,
`schemas/metrics.schema.json`, and the contract/output validators. Those paths are
references for maintainers; they need not exist inside this independent repository.
All essential integration examples are included here so this file can travel alone.

If a later platform version changes the interface, compare its schema and actual
validation behavior before updating this guide or the experiment contract. Do not
silently change a frozen running experiment to accommodate drift. Where older prose
and current validators disagree, report the mismatch and use a reviewed compatible
example instead of guessing that undocumented fields are accepted.
