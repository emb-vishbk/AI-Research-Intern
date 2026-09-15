# Build the Penn-Fudan pedestrian-detection research experiment

Read `RESEARCH_INTERN_REQUIREMENTS.md` in this repository before implementing.
This prompt supplies the task-specific brief on top of that guide. Follow the guide
for integration formats, ownership, provenance, testing and current platform limits.
Inspect the repository and any local agent instructions; preserve existing work.
This file must work without access to the AI Research Intern source repository or
earlier chat messages.

Build the experiment here. Begin implementation, run available local checks, and
continue through the independent workload, compatible exports and one verified
Azure baseline run. Do not stop after producing a plan. Make routine implementation
choices explicitly in the README; unresolved compute resources or scientific policy
must not block unrelated code, fixture tests or documentation.

For this task, a human-triggered helper for one Azure job is explicitly in scope.
The guide's deferred live platform integration does not defer standalone Azure
verification. A YAML template or local-only baseline is an intermediate milestone;
the final handoff requires a real Azure training/validation run and verified,
downloaded results. The Intern's autonomous orchestration remains outside this repo.

## 1. Locked research task

| Item | Decision |
| --- | --- |
| Dataset | Penn-Fudan Pedestrian |
| Model family | TorchVision Faster R-CNN |
| Input | An RGB image |
| Output | Pedestrian bounding boxes, class labels and confidence scores |
| Primary objective | Maximize validation bounding-box mAP@0.50:0.95 |
| Primary metric key | `val_map_50_95`, numeric scale 0–1, direction `maximize` |
| Baseline identity | `EXP-000`, with no parent |

This is supervised pedestrian detection. Instance annotations provide box targets;
the model need not predict masks. Keep the workload independently usable for
training, inference and evaluation without AI Research Intern.

The optimization problem is:

```text
maximize over θ ∈ S:
    f(θ) = bbox mAP@0.50:0.95(Train(Faster R-CNN, D_train; θ), D_val)

subject to:
    training wall time ≤ T_max
    peak GPU memory ≤ M_max

with a bounded total experiment count and total compute allowance.
```

`θ` represents permitted training code/configuration changes. The model weights
result from training. `S` is the declared intervention space. Trusted workload
evaluation computes scores; the platform later decides improvement and continuation.

### Reuse the official implementation

Use the official PyTorch/TorchVision implementation as the starting workload.
Adapt the relevant source into this independent experiment repository and build our
CLI, fixed experiment policy, output export and Azure workflow around it. Do not
reimplement the detector architecture, training primitives or AP calculation.

| Part | Official source to reuse |
| --- | --- |
| Detector and pretrained weights | Installed TorchVision `fasterrcnn_resnet50_fpn` and `FastRCNNPredictor` |
| Penn-Fudan dataset and model adaptation | [Tutorial source in pytorch/tutorials](https://github.com/pytorch/tutorials/blob/main/intermediate_source/torchvision_tutorial.py) |
| Training and evaluation helpers | [pytorch/vision/references/detection](https://github.com/pytorch/vision/tree/main/references/detection): relevant parts of `engine.py`, `utils.py`, `coco_utils.py`, `coco_eval.py` and their required imports |
| Tutorial explanation | [TorchVision detection fine-tuning tutorial](https://docs.pytorch.org/tutorials/intermediate/torchvision_tutorial.html) |

The tutorial's full training example uses Mask R-CNN; its pretrained-model section
also shows Faster R-CNN. Use that Faster R-CNN construction and bbox-only evaluation
for our locked task. The helper sources are in `pytorch/vision`, separately from the
tutorial text/code in `pytorch/tutorials`.

Copy only the required source/functions and their dependencies. Record exact upstream
commits, original paths, dependency compatibility and local modifications in the
README. Preserve the applicable [tutorial license](https://github.com/pytorch/tutorials/blob/main/LICENSE)
and [TorchVision license](https://github.com/pytorch/vision/blob/main/LICENSE) notices.
Do not make installation, import or a training job fetch helper code from mutable
`main` URLs. The model itself should remain a pinned library dependency.

Turn top-level tutorial execution into explicit commands/functions; remove automatic
downloads and demonstration-only runs. Keep adaptations limited to our detector,
configuration, fixed data/evaluation boundary, resource checks and export interface.
Document these differences instead of claiming an exact reproduction of the tutorial.

Upstream `engine.py` contains both training and evaluation. Keep the reused evaluator
and every dependency that determines its score protected, even when training code is
editable. Split the relevant functions into appropriate modules or use protected
helpers with narrow candidate configuration; evaluation must not import editable
training/augmentation code. Adapt bare helper imports to avoid module shadowing.

## 2. Build a simple, legitimate baseline first

Implement the obvious initial workflow:

```text
load approved Penn-Fudan data and fixed split
→ construct Faster R-CNN
→ fit with a basic training loop and simple hyperparameters
→ evaluate validation predictions with the trusted evaluator
→ save checkpoint, measurements and provenance
```

Use these **proposed development defaults** to make the initial implementation
concrete. They are starting choices, not previously approved experiment policy or
a tuned recipe. Put them in explicit configuration and record them for review
before freezing the measured baseline:

| Setting | Starter value |
| --- | --- |
| Model | `torchvision.models.detection.fasterrcnn_resnet50_fpn` |
| Initialization | Explicit `FasterRCNN_ResNet50_FPN_Weights.COCO_V1`; replace the box predictor for background plus pedestrian |
| Optimizer | SGD, learning rate `0.005`, momentum `0.9`, weight decay `0.0005` |
| Batch size | `2` |
| Training duration | `5` epochs, also fixed for subsequent comparisons in the initial search |
| Scheduler / warmup | None for the baseline |
| Augmentation | No optional augmentation for the baseline |
| Trainable backbone layers | `3` |
| Seed | `42`, recorded with determinism settings |
| Checkpoint scored | Final epoch, with the same rule for every candidate |

Keep required tensor conversion and the model's normalization/resizing correct.
Pin the remaining model/inference settings and compatible dependency versions using
the [official model reference](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.detection.fasterrcnn_resnet50_fpn.html).
These development defaults define our adapted baseline, not the tutorial's exact
recipe. In particular, the reference `train_one_epoch` enables first-epoch warmup;
make that behavior explicit/configurable so the chosen baseline configuration is
actually respected. Inspect the pinned source for other implicit training behavior.

Do not run a hyperparameter search or manually develop improved candidates during
this build. We want the untuned baseline to leave a useful research problem for the
Intern. Do not break preprocessing, corrupt labels, select a favorable split, or
fabricate a low score to create headroom. If the baseline is already strong, report
the measured result honestly. No baseline score or amount of improvement is promised.

Every compared trial starts from the same approved initial weights and the same
seeded head initialization. Parent code selection must not silently resume learned
parent weights. Record initialization and trained-checkpoint hashes separately.

## 3. Data and fixed evaluation

Implement explicit Penn-Fudan acquisition/preparation using an official source,
or accept an existing local copy. No downloads at import time. Record source,
checksums, image IDs and annotation provenance in small manifests. Keep large assets
and generated runs outside the candidate source checkout.

As a development split proposal, use 120 training, 25 validation and 25 final-test
images from the 170-image dataset. Start from sorted image IDs and a seeded split;
persist exact membership instead of re-splitting each run. Review known related or
duplicate images before freezing membership and keep such groups together, adjusting
counts transparently if necessary. Do not choose membership based on model scores.
The [dataset tutorial](https://docs.pytorch.org/tutorials/intermediate/torchvision_tutorial.html)
provides the data-format reference.

Keep final-test images and labels outside the runtime editing/development workspace.
Training receives only training examples. Trusted evaluation accesses validation
references; the final test is reserved for a separate audit of the selected model.
Do not report a repeatedly consulted validation score as an independent test result.

Use one foreground class, pedestrian (`1`), with background (`0`) internal to the
detector. Convert each nonbackground instance mask to a valid box through trusted
data code. Document the pixel/box coordinate convention, apply it consistently, and
test edge cases. Box-aware training transforms must preserve valid targets.

Reuse the official COCO helpers backed by `pycocotools` for bounding-box evaluation.
Export the computed numeric evaluator results directly; do not parse printed logs
or rewrite AP. Freeze IoU thresholds 0.50 through 0.95
in increments of 0.05, 101 recall thresholds, all-area primary AP and a maximum of
100 detections per image. Pin the evaluator version and all inference settings,
including score filtering, NMS and image resizing. AP50 and AP75 may be diagnostics.
These metric conventions follow the [COCO evaluator](https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/cocoeval.py).

Evaluation must cover every validation image, including images with zero detections.
Represent empty detections explicitly and support the all-empty prediction case.
Reject missing/duplicate image IDs, invalid classes, nonfinite values, inconsistent
array lengths and malformed/out-of-bounds boxes. Do not silently drop hard examples
or convert an evaluator error or undefined metric into a successful score.

## 4. Candidate boundary and constraints

Implement support for this proposed initial `S`, but do not search it during setup:

- Learning rate, optimizer (SGD or AdamW), weight decay.
- Scheduler/warmup configuration.
- Training-only horizontal flips and modest color augmentation.
- Batch size and backbone freezing/unfreezing.
- Corresponding training implementation changes within the declared editable scope.

Keep the detector/backbone architecture, starting checkpoint, five-epoch comparison
policy, labels, dataset membership, evaluation/inference protocol and seed protocol
fixed. No additional training data, alternative detector or ensemble in this initial
scope. Changes to these choices require a new research configuration.

Store allowed choices and finite parameter bounds in a protected workload policy
file. Propose reasonable bounds in the implementation and document them for review;
do not treat them as already human-approved. Validate candidate configuration before
training. Keep model construction and architecture checks in trusted code so a
candidate checkpoint cannot silently substitute a different network. Filesystem
protection and configuration checks alone are not an execution sandbox.

Implement trusted resource measurement with documented scope and units:

- `train_wall_time_s`: synchronized elapsed training time over the full training
  phase; define exclusions such as data download, initialization and validation.
- `peak_gpu_memory_mb`: define MB explicitly and measure peak allocated CUDA memory
  across the documented trial phase; label it as allocator memory, not total device
  usage. Reset/synchronize measurements appropriately.

CPU fixture runs must not fabricate a GPU-memory value. Omit unavailable optional
metrics and refuse an export that needs an unavailable hard-constraint metric.
Baseline and candidates must use the same hardware/protocol and ceilings. Add
protected limit checks and preserve measured violations; a live job's external
timeout/resource enforcement remains an executor/operator responsibility.

`T_max`, `M_max`, total compute allowance and autonomous experiment count are still
unset. Do not invent approved numerical budgets, add an unrelated latency SLA, or
claim cloud billing from workload timing. A development timing pilot may inform
these choices. Freeze policy before accepting `EXP-000`; rerun measurements when
the finalized setup differs materially. Baseline import requires all hard constraints
to pass. Leave the optional score target absent until baseline evidence supports it.

## 5. Keep the implementation small and independent

Use ordinary typed Python, PyTorch/TorchVision, `pycocotools`, minimal configuration
support and an ordinary test runner. Pin a working environment appropriate to the
available host; do not guess CUDA compatibility. No platform package imports.

Follow the portable guide's suggested layout, adapting names only when useful.
Use `src/experiment/` with these responsibilities:

- `candidate/`: editable training and training augmentation.
- `data/` and `model.py`: protected data/target handling and model construction.
- `evaluation/`: protected prediction validation and scoring.
- `integration/`: protected trial wrapper, identity, provenance and output export.

Use an editable `configs/candidate.yaml`, a protected baseline configuration,
protected evaluation/search/resource policy, and protected data/split manifests.
Initially the candidate configuration must reproduce the baseline recipe. Preserve
the original baseline configuration after optimization begins. Protect dependency
files and tests that establish the scientific boundary too.

Provide documented noninteractive commands for data preparation, one trial,
checkpoint inference, trusted evaluation and tests. Support image inference that
writes boxes/labels/scores to JSON; optional box overlays are useful artifacts.
Visualization thresholds must not change authoritative evaluation.

Implement the trial convention from the guide, for example:

```bash
python -m experiment.integration.run \
  --config configs/baseline.yaml \
  --data-root /path/to/prepared/data \
  --output-dir /path/to/new/baseline \
  --experiment-id EXP-000 \
  --parent-experiment none
```

Ordinary standalone trials must also work without platform identity or loading the
platform contract; distinguish their local evidence from importable exports.
All paths are caller-supplied or portable defaults. Never overwrite previous evidence
or modify protected source, input data or Git state during a trial.

## 6. Prepare the existing platform interface

Implement all output formats and failure behavior from
`RESEARCH_INTERN_REQUIREMENTS.md`, including:

```text
experiment_outputs/
├── run.json
├── metrics.json
├── metrics_history.json
├── logs/
└── artifacts/
```

Write directly into the requested output directory. Use `val_map_50_95` consistently
in the objective, primary metric and numeric metrics mapping. Keep epoch history as
an object of numeric arrays. Save the final checkpoint, actual validation predictions,
effective configuration and provenance as artifacts. No canned measurements.
Provenance must tie results to exact source, data/split, initial and trained weights,
dependencies, seed, hardware and measurement protocol.

Publish completion only after required outputs succeed. Preserve failures with
nonzero exit status and diagnostics. A completed run with a constraint violation
still reports its actual measurements; a crash is not a valid low-scoring result.
Candidate IDs come from future platform requests, never from a workload-owned counter.

Create `.research_intern/contract.yaml` using only the guide's supported version 1.0
fields. Set the objective to `val_map_50_95` / `maximize`, declare actual editable and
protected paths, and use `azure_ml` with `execution/azure_job.yaml`. Do not add custom
model, dataset, search-space or CLI fields to this schema.

Until resource policy is finalized, retain `budget.max_experiments: 0`, omit the
target and unknown optional budgets, and clearly label the setup as incomplete for
autonomous research. An interim `constraints: {}` is scaffolding only; the constrained
research handoff needs approved numeric limits for the measured resource metrics.
Do not write `null` or textual placeholders into numeric contract fields.

Start with the guide's Azure command-job template, then resolve its resource and
input/output bindings for the live verification below. The template alone does not
complete this task. Actual authentication and execution use operator-provided access;
credentials must stay outside committed source, workload policy and model context.

## 7. Verify one real Azure baseline

Implement a small operator helper, for example `tools/azure_run.py`, using Azure ML
SDK v2. Keep its dependencies optional for local workload use, and protect its code
from runtime candidate editing. It manages one explicitly requested job; it does not
select candidates, allocate platform experiment IDs or run an optimization loop.

Implement and document operations equivalent to:

```text
submit: snapshot committed source, bind inputs, submit, persist job receipt
status: inspect the existing job using its receipt
collect: wait for that job to finish, download outputs, validate and report results
```

A submit-and-collect option should let the researcher trigger the entire lifecycle
with one command. Collection must also work in a new process using the receipt, so
closing a terminal or losing a connection does not require another training job.
Use `jobs.create_or_update`, `jobs.get` and `jobs.download` through an authenticated
`MLClient`; cancellation can use `jobs.begin_cancel`. Verify APIs against the installed
SDK and [official JobOperations reference](https://learn.microsoft.com/en-us/python/api/azure-ai-ml/azure.ai.ml.operations.joboperations?view=azure-python).

The helper must:

- Accept explicit workspace/subscription/resource-group settings and resolved compute,
  environment, data and initialization-checkpoint references. Keep account settings
  separate from scientific configuration. Use existing authorized resources; report
  missing access/resources precisely instead of inventing them.
- Submit a source snapshot matching the recorded Git commit. Exclude secrets, Git
  internals, local environments, caches, generated results and final-test data. Pass
  source identity explicitly to the trial; an uploaded snapshot may not contain `.git`.
- Bind the baseline configuration, `EXP-000`, and its null-parent CLI convention to
  the job. The remote command runs training followed by trusted validation and writes
  all standardized outputs to the named `experiment_outputs` destination.
- Persist a chosen unique Azure job name and request/source identity before submission,
  then record the returned job ID and workspace identity immediately. Reconcile an
  uncertain submission outcome against that name before any retry. A collection retry
  must never submit a new job. Keep receipts outside committed source.
- Poll with a bounded interval, preserve terminal failure details and retrieve available
  diagnostics. Download the named output into a fresh temporary location, locate and
  verify its expected files, then publish the verified download without overwriting
  earlier evidence. Do not assume SDK download nesting equals the workload output root.
- Validate downloaded identity, primary/constraint metrics and artifact references.
  Report actual baseline mAP, resource measurements and constraint eligibility. Remote
  trusted evaluation computes mAP from predictions; local validation checks the export.
  The later platform owns parent/best comparisons and `KEEP`/`REJECT` decisions.

The required live acceptance evidence is one completed Azure baseline training job,
its job name/workspace reference, exact source/configuration/data/weight provenance,
downloaded standardized outputs and artifacts, and a passing local export check.
The baseline must satisfy the finalized constraints. A template, successful submission
alone, or a synthetic smoke test cannot satisfy this acceptance criterion.

Run this check when the researcher supplies the required access and approved compute
limits. Do not initiate unrequested resource provisioning or an unbounded paid run.
If access or policy is missing, complete the helper and mocked tests, document the
exact next command and missing inputs, and mark live verification incomplete.

For the later Intern handoff, retain the ordinary training/evaluation entry point,
job definition and standardized outputs. Its Azure adapter will submit that same
workload for committed candidates and own polling, collection, budgets and recovery.
The standalone helper remains useful to the researcher. Copying the source into the
platform's staging directory does not automatically connect Azure or import a baseline.

## 8. Build and verify in vertical slices

1. Inspect this repository, read the portable guide, record locked decisions and
   proposed defaults, and establish the package/environment. Select compatible,
   pinned official tutorial/helper sources and record their provenance.
2. Adapt the official Penn-Fudan dataset/box conversion and COCO evaluation helpers;
   add known-answer tests for our fixed scientific boundary.
3. Use TorchVision's Faster R-CNN and adapt the reference training routine into the
   simple configured recipe, with our one-trial wrapper.
4. Exercise training → predictions → trusted scoring → standardized outputs on tiny
   local fixtures with actual computation. Keep this evidence separate from `EXP-000`.
5. Add inference, provenance, failure handling, contract configuration and the Azure
   job definition plus operator helper. Mock submission/status/download in ordinary tests.
6. Run deterministic tests and a small integration smoke test within available
   resources. Keep full-detector, real-data, pretrained-download and GPU tests explicit
   opt-in paths. A tiny fixture proves plumbing, not baseline model quality.
7. With approved data/checkpoints, Azure resources and finalized policy, submit the
   real baseline, collect its outputs and verify the measured result as described in
   section 7. Otherwise finish all independent work and report the exact remaining
   input and command needed. Keep the Azure acceptance milestone incomplete.

Tests should cover valid/invalid mask-to-box conversion, split disjointness, box-aware
transforms, perfect/wrong/empty detections, malformed predictions, policy violations,
matching export identity and primary metric, nonfinite/missing required metrics,
failure preservation and protection of existing outputs/source. Tests must run
without AI Research Intern, Copilot or Azure. No default network/model downloads.
Do not require a full ResNet-50 backward pass in every ordinary unit-test invocation.

Also test the operator helper with mocked Azure responses: persisted job receipts,
ambiguous submission reconciliation, resumed collection without duplicate submission,
terminal failures, transient download errors and named-output directory handling.

Do not implement the Intern controller, ledger, autonomous repetition, platform cloud
orchestration, UI or an optimization framework here. The one-job operator helper is
in scope. The current platform's normal run command uses synthetic fixtures; this
project's readiness does not mean live platform integration already works. Do not
document fictional platform commands.

## 9. Completion report

Update the README with exact setup, data, training, inference, evaluation, Azure
submission/status/resumed-collection and test commands. Keep scientific policy and
unresolved values visible. Report:

- What was implemented and which commands/checks actually ran.
- Which defaults/policy still need researcher decisions.
- Whether the required Azure baseline completed and, if so, its Azure job/workspace
  reference, actual mAP, resource usage, exact source/artifact references, downloaded
  evidence location and constraint eligibility.
- Whether exports were checked locally, against the real platform, or neither.
- What remains before the first live platform candidate.

The intended deliverable is a correct, untuned pedestrian detector with a trustworthy
evaluation boundary and a traceable baseline verified on Azure, ready for subsequent
constrained research. Begin building that smallest complete workload now.
