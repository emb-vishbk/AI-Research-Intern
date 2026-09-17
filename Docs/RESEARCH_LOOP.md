# AI Research Intern

## RESEARCH_LOOP.md

## 1. Purpose

This document defines the runtime protocol of the autonomous research loop. It explains how the system moves from `EXP-000` to later experiments, how evidence becomes the next research decision, and when the loop stops. `PROJECT.md` defines the thesis, `MVP_SCOPE.md` defines the boundary, and `ARCHITECTURE.md` defines technical ownership. This file is the behavioral source of truth for the Research Controller.

## 2. Core Loop

The required lifecycle is:

```text
research state · → observe · → diagnose · → hypothesize · → plan · → implement · → validate
→ version · → execute · → collect · → evaluate · → record · → rebuild research state
→ repeat or stop
```

The MVP runs this loop serially. Only one Copilot research iteration and one Azure ML experiment may be active at a time.

## 3. Responsibility Split

Copilot performs repository understanding, evidence interpretation, hypothesis generation, candidate planning, and controlled code modification. The controller owns orchestration, state transitions, permissions, budgets, stopping, and recovery. Git owns exact historical code states. Azure ML executes computational experiments. The evaluator determines whether a candidate improved. The ledger stores scientific history. The research-state builder creates compact working memory for the next Copilot session. Copilot never grades its own experiment.

## 4. Experiment Identity

Every executed scientific run receives an immutable experiment ID.

```text
EXP-000 = human baseline · EXP-001 = first autonomous experiment
EXP-002 = second autonomous experiment · ...
```

Bootstrap or integration work happens before `EXP-000` and is not an experiment. Every experiment except `EXP-000` has a parent experiment. Experiment IDs describe scientific lineage; Git commits describe code lineage.

## 5. Preconditions

The controller may enter the autonomous loop only when the experiment contract is valid. The objective must be machine-evaluable. Editable and protected surfaces must be explicit and non-overlapping. Execution configuration and required output paths must exist. Budgets must be defined. The experiment repository must be a valid Git repository. Azure access must be available. A baseline must be importable or runnable. If any hard precondition fails, autonomous research does not start.

## 6. Establishing `EXP-000`

The human baseline becomes `EXP-000`. It may be imported from an existing successful Azure job or produced by a baseline run. Its result must be normalized into the same contract used by later experiments. Record at least:

```text
experiment_id = EXP-000 · parent = none · Git commit · Azure job reference · objective metric
constraint metrics · run status · result artifacts
```

Persist `EXP-000` in the ledger before autonomous research begins.

Local source upload/preparation precedes this lifecycle. Its import commit and
`workspace.json` preserve code and checks, but allocate no experiment ID, create no
synthetic baseline, and consume no experiment slot. A source-only project can be
reviewed while the objective remains undecided. A valid core contract is needed
before Git preparation; the optional fixed evaluation protocol must be supplied and
explicitly confirmed before the future real run is considered evaluation-ready.
Changes after preparation require reconciliation, never an implicit reset or policy
replacement. Result collection checks the evaluation fingerprint, when configured,
before the existing deterministic metric comparison. Live start remains disabled.

## 7. Initial Research State

After recording the baseline, derive the first compact research state. Example:

```json
{
  "objective": "...",
  "baseline": "EXP-000",
  "best": "EXP-000",
  "last_experiment": "EXP-000",
  "supported_directions": [],
  "rejected_directions": [],
  "open_questions": [],
  "remaining_budget": {}
}
```

The exact schema may evolve. The ledger and contract remain the source of truth; research state is a derived working view.

The offline state builder derives bounded, attributable findings from recorded
decisions and keeps baseline, best, and latest distinct. Generated open questions
are prompts for investigation, not inferred scientific conclusions. State exports
are disposable; rebuilding uses a consistent ledger snapshot and persisted limits.

## 8. Beginning an Iteration

Before creating `EXP-N`, the controller checks whether research is allowed to continue. Check the target status, remaining experiment budget, remaining Azure compute budget, AI/Copilot budget when available, human stop state, and whether another run is active. If any mandatory stopping condition is met, do not start a Copilot session. The current offline handoff produces a draft plan without allocating an experiment ID. Candidate reservation assigns the ID when a prevalidated code state enters execution; the future live candidate lifecycle must preserve that identity through its own preparation steps.

## 9. Parent Selection

Each new experiment starts from a selected parent. For `EXP-001`, the parent is normally `EXP-000`. For the MVP, later experiments should usually start from the current best valid experiment. The most recent experiment is not automatically the parent. Example:

```text
EXP-003  F1=.846   ← best
├── EXP-004  F1=.821   REJECT
└── EXP-005             next candidate
```

After a regression, the next candidate should normally branch from the stronger parent rather than inherit the regression.

## 10. Preparing the Workspace

Resolve the selected parent's Git commit. Reset the reusable experiment workspace to that commit. Verify the repository, branch/HEAD state, and clean or expected working-tree state. Do not silently destroy unrelated human work. For the hackathon, use a dedicated experiment repository/workspace so resets are predictable. The selected parent commit becomes the code base for `EXP-N`.

## 11. Building Copilot Context

The offline candidate controller now restores the actual selected-parent commit
before constructing a fresh editor's context, which includes the persisted full
core contract. It refuses dirty/unrecorded workspace state instead of forcing a
reset. The older plan-only handoff demonstration still supplies only a parent reference.

Prepare only the information needed to choose and implement the next experiment. Include:

```text
objective and target · constraints · experiment contract · editable paths · protected paths
current research state · selected parent · parent metrics · recent experiment summaries
relevant metric history · relevant logs · remaining budget · implementation instructions
```

Do not dump the full ledger, every artifact, or all historical logs into the prompt. Context should be evidence-rich but compact.

## 12. Fresh Copilot Session

Create a fresh Copilot SDK session for each experiment iteration. Set its working directory to the prepared experiment repository. Provide the current research context and explicit task. Copilot may inspect relevant code and evidence, modify editable files, and run permitted local commands. It must not rely on previous chat memory. Continuity comes from the ledger, research state, and code state on disk.

## 13. Observe and Diagnose

Copilot first inspects evidence before proposing a change. Relevant evidence can include objective metrics, training/validation curves, recent experiment outcomes, failures, code, configs, and remaining budget. Example:

```text
training loss continues decreasing · validation loss rises after epoch 9
validation F1 peaks and then falls
```

From this, Copilot may diagnose likely overfitting. Diagnosis remains provisional rather than being treated as truth. Its purpose is to generate a useful testable intervention.

## 14. Hypothesis and Candidate Plan

Copilot should propose one coherent hypothesis and intervention per MVP iteration. Example:

```text
Observation: validation loss rises while training loss falls.
Hypothesis: the model is overfitting after approximately epoch 9.
Intervention: increase weight decay and restore the best validation checkpoint.
Expected effect: improve validation F1 without violating latency constraints.
```

Prefer changes that answer a clear research question. Avoid bundling many unrelated modifications when a smaller experiment can isolate the effect.

## 15. Structured Research Decision

Before implementation, create a structured candidate plan containing:

```text
experiment_id · parent_experiment · observation · diagnosis · hypothesis · proposed_change
expected_effect · expected_files · risk_or_cost_notes
```

This is a concise research decision record, not hidden chain-of-thought. It must be serializable and persisted with the experiment. The controller may validate the plan before allowing code changes.

## 16. Plan-Level Policy Check

Verify that the candidate fits the experiment contract. The proposed change must stay inside editable scope. It must not require modifying protected evaluation logic, test data, objective, constraints, budgets, or contract policy. It must fit remaining experiment and compute budgets. If the plan violates policy, reject it before Azure execution. A non-executed invalid proposal need not become a scientific experiment result.

## 17. Candidate Implementation

Copilot modifies the selected parent code according to the candidate plan. It may inspect more files while implementing and run allowed local commands needed to keep the candidate coherent. Only editable surfaces may change. Prefer the smallest reasonable implementation that tests the hypothesis. Avoid unrelated refactors and cleanup unless they are required for the experiment. When finished, Copilot returns a concise structured implementation summary.

## 18. Filesystem and Diff Verification

The controller does not trust the Copilot summary as proof of what changed. The Git/workspace layer inspects the actual filesystem and computes the diff against the parent commit. The actual diff is authoritative. Check every changed path against editable and protected scopes. Generate a human-readable patch for the ledger/UI. Any protected-file modification blocks Azure submission until reverted or repaired.

## 19. Pre-Flight Validation

Run cheap deterministic checks before spending Azure GPU compute. Minimum checks:

```text
protected-file check · syntax check · contract/config validation · candidate metadata validation
budget check · optional lightweight smoke test
```

Project-specific smoke tests may be declared by the experiment contract. The MVP does not require a general sandbox. Validation outcomes should be recorded and surfaced to the UI.

## 20. Pre-Flight Failure

If the candidate fails validation before Azure submission, do not consume remote compute. Preserve the diagnostics and candidate plan. Restore the selected parent if the workspace must be cleaned. The controller may ask Copilot to repair the candidate or generate a replacement. Do not infer model-quality conclusions from a candidate that never ran successfully. Preflight failure is an implementation failure, not a scientific regression.

## 21. Versioning the Candidate

In the offline workflow, failed preparation attempts keep context, plan and
diagnostics outside the experiment repository. The loop archives changed files
before restoring a failed attempt whose filesystem still matches its recorded
snapshot. Newer changes require inspection. Failed attempts count toward a separate
preparation cap; no experiment slot is consumed before successful reservation.
Preflight checks syntax without executing code. The next fresh mock editor receives
concise preparation failures alongside experiment-derived research state.

A candidate that passes required preflight checks becomes the executable code state. Commit the exact candidate to Git. Record the candidate commit SHA and its parent commit. Map both to `EXP-N`. Conceptually:

```text
EXP-N · ↔ parent experiment · ↔ parent commit · ↔ candidate commit
```

Azure must execute the committed candidate state, not an untracked later workspace state.

## 22. Azure Submission

The offline candidate controller writes commit evidence before reserving `EXP-N`,
then returns the persisted `PREPARED` record. The execution controller handles
submission separately. Commits use stable refs independent of their later experiment
ID so rejected and interrupted candidates survive checkout. In initialized offline
loop runs, a journaled validated commit can be completed or adopted after interruption.
The reservation transaction links it to exactly one experiment ID. Recovery verifies
the recorded code and parent before reserving; changed history or a human stop blocks
new reservation.

The controller gives the validated candidate to the Azure ML executor. Submit the configured Azure job. Immediately persist:

```text
experiment ID · candidate commit · Azure job ID · submission time · status
```

Persist the Azure job ID before waiting for completion. This allows recovery after local process interruption. Copilot becomes inactive once a valid candidate has been handed off for execution.

## 23. Azure Running State

During remote execution:

```text
Copilot      inactive
Controller   polling
Azure ML     running
```

The controller periodically checks Azure status. The browser may show the job ID, status, elapsed time, and current experiment. Simple polling is sufficient for the MVP. Do not spend LLM tokens waiting for training to finish.

## 24. Azure Failure

Azure may fail because of Python errors, CUDA OOM, NaNs, invalid checkpoints, missing data, environment issues, or infrastructure failure. Record the experiment as `FAILED`. Preserve its exact code commit, Azure job ID, logs, and failure type. Do not treat execution failure as objective regression. The next Copilot context should include enough failure evidence to decide whether repair is worthwhile.

## 25. Result Collection

When Azure finishes successfully, download the standardized outputs. Expected structure:

```text
run.json · metrics.json · metrics_history.json · logs/ · artifacts/
```

Persist the outputs under the experiment record. The collector validates required presence and structure. It does not decide whether the experiment was good. Large artifacts stay on disk; structured metadata is entered into the ledger.

## 26. Output Contract Failure

If required outputs are absent or invalid, the experiment cannot be scored normally. Mark it as failed or invalid according to controller policy. Preserve whatever evidence exists. The evaluator must not guess the objective metric from arbitrary logs or prose. The standardized output contract is required for deterministic evaluation.

## 27. Deterministic Evaluation

For a valid completed run, evaluate the candidate against the experiment contract. Compare candidate score with the parent score and current best score. Check all hard constraints. Check whether the target has been reached. Produce fields such as:

```json
{
  "improved_over_parent": true,
  "new_best": true,
  "constraints_satisfied": true,
  "goal_reached": false,
  "decision": "KEEP"
}
```

The decision must be reproducible from recorded metrics and rules.

## 28. `KEEP`, `REJECT`, `FAILED`, `GOAL_REACHED`

Use `KEEP` for a valid candidate that should remain eligible as a future parent, normally because it improves the objective while satisfying constraints. Use `REJECT` for a completed valid run that should not become the active parent, such as an objective regression or hard constraint violation. Use `FAILED` when execution or result production prevents meaningful evaluation. Use `GOAL_REACHED` when the target and all required constraints are satisfied. A run can be a new best without reaching the final target. A rejected or failed run remains part of research history.

## 29. Example Outcome Branches

Improved but target not reached:

```text
EXP-003 = .846 · EXP-004 = .852   KEEP / NEW BEST · target  = .870
```

Regressed:

```text
EXP-003 = .846   BEST · EXP-004 = .821   REJECT · next parent → EXP-003
```

Failed:

```text
EXP-004 FAILED · reason → CUDA OOM · next iteration → repair or try another candidate
```

Goal reached:

```text
target >= .870 · EXP-007 = .874 · constraints satisfied · → GOAL_REACHED
```

## 30. Recording the Experiment

After evaluation, append the experiment to the ledger. Persist at least:

```text
experiment ID · parent experiment · parent commit · candidate commit · observation · diagnosis
hypothesis · candidate plan · code diff · Azure job ID · execution status · metrics
constraint results · decision · conclusion · budget usage
```

The experiment is not fully integrated into research memory until this record exists.

## 31. Conclusion and Best-Experiment Update

Create a concise conclusion grounded in actual results. Examples:

```text
Supported: increased weight decay improved F1 and reduced the train/validation gap.
Rejected: dropout=.25 reduced F1 under the current regularization settings.
Failed: batch size 32 exceeded available GPU memory.
```

Distinguish observation from interpretation. Update the global best pointer only through deterministic evaluation. The best experiment may differ from the latest experiment and current Git HEAD.

## 32. Rebuilding Research State

After `EXP-N` is recorded, rebuild compact research memory from the ledger. Include:

```text
objective and target · baseline · current best · last experiment · recent experiments
supported directions · rejected or harmful directions · known failures · open questions
remaining experiment budget · remaining compute budget
```

The state should be concise enough for repeated Copilot use. It must always be reconstructable from persisted history.

## 33. Handoff to the Next Iteration

The required handoff is:

```text
Azure result · → deterministic evaluation · → ledger · → research state · → fresh Copilot session
```

Do not feed raw Azure results directly into an unstructured repeated prompt. Normalization through evaluation and persistent state turns independent trials into cumulative research. The next session receives the rebuilt state plus only the relevant parent/recent evidence.

## 34. Outcome-Specific Handoff

After a `KEEP`, expose the improvement, remaining weaknesses, and promising direction. After a `REJECT`, restore a stronger parent while preserving the negative finding. After a `FAILED`, expose the failure type, relevant change, logs, and likely repair direction. Example:

```text
EXP-004 failed: CUDA OOM · change: batch_size 16 → 32 · selected parent: EXP-003
possible repair: retain effective batch size using gradient accumulation
```

The next iteration should learn from failure rather than repeat it blindly.

## 35. Exploration and Exploitation

The MVP does not require a formal optimizer. Copilot may choose to exploit a promising direction, explore a distinct intervention, repair a failure, or test an unresolved hypothesis. Its choice should use research state and remaining budget. Later versions may add TPE, Bayesian optimization, evolutionary search, or explicit parent-selection policies. Do not delay the first working loop for advanced search.

## 36. Budget Accounting

Update budget usage after each meaningful action. Track executed experiment count and Azure compute usage at minimum. Track Copilot/AI usage when practical. A preflight failure should not consume Azure GPU budget. An Azure job that consumed compute but failed still counts toward compute usage. The controller is the final budget authority. Copilot cannot increase limits or ignore exhausted budgets.

The current experiment-count guard conservatively counts every reserved candidate
slot, even if submission or execution subsequently fails. Baseline, draft proposals,
and validation rejected before reservation do not count. The immutable limit is
persisted before baseline import and checked atomically at reservation. Reopening
cannot replenish it; the last allocated slot remains eligible to finish execution.
Compute/account allowances remain unknown until their integrations are available.

The offline `run` command also fixes `max_attempts` before baseline import (default:
twice `max_experiments`). Every preparation is counted before editing, even if it
fails or is interrupted. This application policy bounds repair/replacement attempts
without changing the scientific contract. Resume reconciles already reserved work
before checking limits for another preparation.

## 37. Stopping Conditions

Before every new iteration, evaluate:

```text
goal reached · experiment budget exhausted · GPU/compute budget exhausted · AI budget exhausted
human stop requested · unrecoverable system condition
```

Optional future rules may include stagnation or wall-clock limits. If a hard stop is true, no new Copilot session begins. If the goal is reached, the normal search loop terminates successfully.

## 38. Human Stop

The researcher may stop the run through the UI or application interface. If no Azure job is active, stop before the next iteration. If a job is active, the implementation may allow completion or explicitly cancel through the Azure executor. Whichever behavior is chosen must be visible and deterministic. Never launch another candidate after a stop request has been accepted.

## 39. Restart Recovery

Persist enough state to recover after local process interruption:

```text
current experiment ID · selected parent · candidate commit · Azure job ID · lifecycle status
ledger state · best experiment · budget usage
```

On restart, reconcile local state with Azure before launching anything new. If the Azure job is still running, resume polling. If it completed, collect and evaluate it. Do not accidentally submit the same candidate twice.

The offline loop implements these local recovery cases:

| Persisted state | Resume action |
| --- | --- |
| Recorded failed preparation | Archive changed files, restore the recorded parent, then retry within the preparation cap. |
| Interrupted validated commit | Verify recorded files and parent; finish Git or adopt its existing exact commit, then reserve once. |
| Reservation completed | Finish publishing candidate evidence using the existing ID. |
| `PREPARED` | Submit the reserved candidate unless human stop is set, even if it used the final slot. |
| `SUBMITTING` | Attach a matching durable simulated job; if absent, record submission failure without resubmission. |
| `SUBMITTED` / `RUNNING` | Continue polling or collecting the same job; retry temporary collection failures. |
| Terminal experiment | Rebuild state and continue or stop; normal completion restores best recorded code. |

Each driver holds a process lock, so managed recovery cannot overlap another
preparation. A matching stale candidate marker is removable only under that lock.
Interrupted dirty edits without a recorded final snapshot, unknown markers, newer
files and Git tampering stop for manual inspection. Partial restoration is journaled
before mutations and can resume from the recorded original/failed file states.
An interrupted unreserved commit is retained if human stop blocks reservation.
Human stop remains set across resume; collecting an already submitted job does not
authorize a new candidate. Initial baseline setup and live Azure recovery are deferred.

## 40. Idempotency

Controller operations should be safe to resume. Avoid duplicate Azure submissions, duplicate ledger rows, duplicate experiment IDs, and accidental artifact overwrites. Use persisted lifecycle status to determine the next legal transition. An experiment should advance monotonically through its lifecycle except for explicit recovery handling. Git commit and Azure job ID are key recovery anchors.

## 41. UI Event Stream

Expose major lifecycle events:

```text
research_started · parent_selected · copilot_started · candidate_planned · files_modified
preflight_started · preflight_passed · candidate_committed · azure_submitted · azure_running
azure_completed · results_collected · experiment_evaluated · ledger_updated · research_state_updated
research_stopped
```

The UI reflects system state; it does not make scientific decisions.

## 42. Research Decision Visibility

For every experiment the human should be able to answer:

```text
What was observed? · What was diagnosed? · What hypothesis was tested? · What code changed?
What exact commit ran? · Which Azure job ran it? · What metrics returned?
Were constraints satisfied? · What did the evaluator decide? · What was learned?
```

This visible evidence chain is central to the hackathon demo. Do not depend on private model reasoning for explainability.

## 43. Loop Invariants

Every executed experiment maps to an exact Git commit. Every Azure job maps to one experiment. Every valid evaluation uses structured outputs. Every failure has an explicit failure record. Every result enters the ledger before another research iteration starts. Research state is derived from persisted history. The latest experiment is never assumed to be the best. Protected surfaces remain unchanged. The evaluator alone determines experimental success. The controller alone decides whether another iteration may begin.

## 44. Complete Example

```text
EXP-000 baseline F1=.812
↓ Copilot observes overfitting
EXP-001: early stopping + weight decay → Azure ML → F1=.831 → KEEP / new best
↓ ledger + research state
EXP-002: moderate dropout → Azure ML → F1=.817 → REJECT
↓ restore EXP-001
EXP-003: mild augmentation → Azure ML → F1=.842 → KEEP / new best
```

Successes and failures both change future search behavior.

## 45. Definition of a Complete Iteration

An experiment iteration is complete only when the parent, hypothesis, plan, implementation, validation, code commit, Azure terminal state, collected evidence, evaluation, ledger update, and rebuilt research state all exist.
Only then may the next autonomous experiment begin.

## 46. Final Mental Model

The loop is not:

```text
ask LLM → try something → ask LLM again
```

It is:

```text
persistent evidence · → bounded reasoning · → controlled code change · → real execution
→ external measurement · → durable scientific record · → updated evidence
→ next bounded reasoning step
```

That cumulative evidence loop is the core behavior of the AI Research Intern.
