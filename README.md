# AI Research Intern

A local research controller for improving an existing ML experiment under fixed
scientific constraints and resource limits.

```text
measured baseline → inspect evidence → fresh Copilot coding turn
→ validate changes → commit exact source → Azure ML job
→ independent scoring → keep/reject/fail → update memory → repeat
```

The researcher defines the objective, evaluator, data, editable scope and limits.
The controller owns Git, Azure, budgets, recovery and decisions. One experiment
runs at a time. The current best remains the parent after a regression.

## Implementation status

Live and offline runs share the serial controller. Live baseline measurement,
restricted Copilot editing, Azure submission/recovery, independent scoring, durable
reservations, browser controls and JSON report export are implemented.

Offline integration tests use real Git, SQLite, HTTP/SSE and scorer processes with
fake remote services. They do **not** demonstrate real model improvement or prove
access to your Azure/Copilot accounts. A measured multi-trial cloud run remains the
final acceptance check after a researcher configures a workload and its limits.
The platform does not select a model/dataset or provision Azure resources for you.

## Install and open

Requirements: Python 3.11+, Git, and a project virtual environment. Use the same OS
for the controller, Git, Azure CLI, Copilot runtime and scoring interpreter.

WSL / Linux, from this directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[azure,web,web-test]'
.venv/bin/python -m research_intern.main serve
```

If `venv` reports missing `ensurepip`, install your distribution's matching
`python3-venv` package first. Keep project packages out of system Python.

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[azure,web,web-test]"
.\start-web.cmd
```

`start-web.cmd` uses a Windows `.venv` when present, otherwise the WSL `.venv`.
Run `start-web.cmd --check` to verify local application imports without starting
the server or signing in. Keep `src/research_intern/` in the application checkout:
project upload creates the experiment workspace, not the application source. If
that folder is missing, restore it from your checkout before launching.
The server prints its loopback address (default `http://127.0.0.1:8000`). It is a
single-researcher local application, not an authenticated public web service.

## Prepare a research project

1. Sign in to Azure and GitHub Copilot, then choose an available coding model.
   GitHub Enterprise is optional; ordinary GitHub Copilot accounts can sign in.
2. Select your existing project folder or ZIP. No prescribed layout, contract or
   settings JSON is required. The importer filters known credential files, Git
   metadata, environments and caches; data, weights and prior results are retained
   separately with their original relative paths. Your original folder is untouched.
   Limits: 2 GiB / 30,000 retained files, including up to 64 MiB of source.
3. Enter a research goal and limits. Review the detected job YAML, metric,
   evaluation script, fixed validation reference and editable training files.
   The app detects ordinary Python `argparse` scoring interfaces and connects
   predictions/checkpoints, the validation reference, local YAML data inputs and
   JSON results automatically. Evaluator helpers and reference configuration are
   protected too. Ambiguous or missing inputs are identified by name; custom
   command/interpreter overrides remain available under additional settings.
4. Load Azure subscriptions, resource groups, workspaces and compute. Select the
   resources returned by your signed-in account, or enter the specifically labelled
   subscription ID and resource names. Validate the selected target.
5. Find jobs by experiment name or exact job name. Select the matching run, or
   explicitly choose a new baseline. A selected running job is monitored with
   Copilot off. Completed/failed runs have their outputs and logs downloaded;
   numeric JSON metrics are shown as observations, not verified scores. Confirm
   the historical source/data association before reusing an existing baseline.
6. Review and select **Prepare research**. The app generates its internal contract,
   freezes evaluation, records source in Git and creates execution settings.
   Preparation checks saved prediction artifacts and prepares a compatible,
   project-owned scoring Python with the evaluator's declared dependencies. The
   first setup may download Python/packages; the app/system environment is not
   modified. A pinned, checksum-verified standalone installer bootstraps this
   environment even when the host Python lacks pip or ensurepip; no sudo or
   Ubuntu package installation is needed. PyTorch scoring uses CPU wheels. Installation diagnostics are in
   `.runtime/research-project/scoring-environments/setup.log`.
   Preparation does not run the workload. It automatically checks Azure metadata and isolated
   Copilot authentication without a model turn or GPU submission. Checks expire
   after 24 hours; they cannot prove that a future submission will succeed.
7. Select **Measure baseline** for a new run, or **Re-score existing baseline** to
   evaluate a selected run's saved predictions/checkpoint without submitting it
   again. Both use the same independent evaluator. No coding turn runs.
8. After baseline acceptance, select **Start loop**. Iterations continue until the
   target, a limit, an unresolved error or a human stop.

The budget form saves a draft. Preparing research applies the reviewed choices,
including when an older source preparation contains placeholder limits. Before live
configuration, the app can revise that preparation while preserving its complete
previous source and receipts in `.runtime/research-project/preparation-history/`.
Once live configuration is saved, limits and evaluation are immutable for that run.
Each job reserves its GPU count multiplied by its maximum running time;
choose a timeout that leaves room for the planned candidate experiments.
Ordinary jobs currently use one node and folder outputs. Pipelines, sweeps and
distributed jobs need a single command-job entry point. Azure environment versions
and data inputs remain defined by the reviewed YAML; use stable versions and
immutable data. The app cannot guarantee that an external URL or image tag is immutable.
The fixed scoring bundle is limited to 64 MiB, 16 MiB per file. Copilot can edit
up to 500 selected UTF-8 source files, each no larger than 128 KiB.
Retained evaluation data stays outside that source bundle, is hash-checked and is
copied into the scorer's isolated working folder. Automatic dependency setup uses
standard declarations in `pyproject.toml` or `requirements.txt` and binary wheels;
it does not execute an uploaded installer. Unsupported interfaces, undeclared
dependencies, remote-only evaluation data and multiple matching artifacts need
specific input resolution; the app never invents a scientific evaluator or a score.

Historical source association is explicitly **user-attested**, stored in the report
alongside the original job and artifact hashes. Re-scoring verifies the metric;
it does not prove what historical code ran. Historical job charges are outside the
new budget. Jobs without evaluable artifacts cannot supply an accepted baseline.
One project is active at a time. Select **Choose another project**, then choose
any folder or ZIP. The replacement is validated before switching; cancelling the
picker or uploading invalid files leaves the current project loaded. Its complete
source, settings, logs, outputs and ledger are retained in
`.runtime/project-history/<id>/`, with the location shown in the dashboard.
Accounts stay signed in, while the new project needs its own goal, budget and
Azure selections. Finish any running experiment or pending recovery before switching.
Do not delete individual ledger, intent or receipt files to bypass recovery checks.

## Legacy command-line configuration

The browser generates configuration from the onboarding form. It has no service
settings upload or separate “Live services” setup. The following format is retained
for existing command-line integrations only.

Existing prepared projects may still use `.runtime/live-settings.json` with reviewed values.
This example shows the shape; it is not an approved budget or working Azure binding.
No credentials belong in it.

For a corporate proxy, `start-web.cmd` loads a locally provisioned
`.runtime/trusted-ca-bundle.pem` for Python/Azure and `.runtime/zscaler-root.pem`
for the Copilot runtime. Only use certificates already trusted by your organization;
TLS verification stays enabled. WSL users can launch the same wrapper with
`.venv/bin/python -B tools/launch_web.py`.

Start normally with `start-web.cmd` (WSL: `.venv/bin/python -B tools/launch_web.py`).
The **Connect your accounts** section is available before project upload:

1. Click **Sign in to Azure**, open the Microsoft link and enter the displayed
   one-time code. Complete your organization's SSO/MFA. This uses the Azure SDK's
   development application by default; operators can set `RESEARCH_INTERN_AZURE_CLIENT_ID`
   and `RESEARCH_INTERN_AZURE_TENANT_ID` for their own registered application.
2. Click **Sign in to Copilot**, then **Continue with GitHub**. For an enterprise
   account, expand **GitHub Enterprise account** and enter its `*.ghe.com` hostname.
   Missing, pinned Copilot helpers are downloaded and checksum-verified on this action.
3. Choose an available coding model and click **Save model**. No settings file is
   needed. If a legacy draft exists it is updated; live run models stay fixed.
4. Follow **Prepare a research project** above. Account checks run automatically
   during preparation; **Retry connection check** is available if a check fails.

Cancel and retry are available without restarting the server. `403` Copilot errors
are displayed as an account/license/policy access problem, separately from sign-in.
Azure and Copilot are independent connections. Sign-in never creates a coding turn,
submits training, approves evaluation or starts research. The old `--sign-in` launcher
flag now opens the dashboard too; it never blocks startup with terminal login prompts.

Azure tokens remain in the backend's memory for this app session. Windows/macOS Copilot
uses the OS credential manager and refuses plaintext fallback. Linux/WSL Copilot uses
a private, owner-only tmpfs directory for the session; no keychain packages or password
commands are required. The app gives the login helper a private terminal to handle
its credential-storage confirmation; piped input can skip that prompt and discard
an otherwise successful login. Only the verified session directory permits this
fallback, and terminal input is not echoed. Normal shutdown removes it; a crash can leave this private
volatile directory until reboot. Reconnect session logins after restarting the app.
Legacy credentials saved by earlier CLI commands are not modified. Never include
credentials intentionally. Known private filenames are filtered; this is not a
secret scanner for arbitrary source content. Larger datasets can use Azure inputs.
See [GitHub authentication](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli)
and [Microsoft device authorization](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-acquire-token-device-code-flow).

```json
{
  "azure": {
    "subscription_id": "00000000-0000-0000-0000-000000000000",
    "resource_group": "your-resource-group",
    "workspace_name": "your-workspace",
    "compute": "your-gpu-compute",
    "environment": "azureml:your-environment:1",
    "image": "your-registry.azurecr.io/runtime@sha256:REPLACE_WITH_64_HEX_DIGEST",
    "timeout_seconds": 600,
    "gpu_count": 1,
    "inputs": {
      "config": {"baseline": "configs/baseline.yaml", "candidate": "configs/candidate.yaml"},
      "dataset": {"type": "uri_folder", "path": "azureml:your-data:1"},
      "weights": {"type": "uri_file", "path": "azureml:your-weights:1"}
    }
  },
  "copilot": {
    "runtime_path": ".runtime/copilot-sdk/REPLACE_WITH_PRINTED_EXECUTABLE_PATH",
    "model": "REPLACE_WITH_APPROVED_MODEL_ID",
    "readable_paths": ["train.py", "configs/candidate.yaml"],
    "max_turns": 3,
    "timeout_seconds": 120
  },
  "scoring": {
    "python": "/absolute/path/to/reviewed-scoring-venv/bin/python",
    "files": ["evaluate.py", "splits/validation.json"],
    "timeout_seconds": 120
  }
}
```

Azure input names are workload-specific. Bindings support scalar parameters,
versioned `uri_file`/`uri_folder` assets, or baseline/candidate source paths.
The controller supplies `experiment_id`, `parent_experiment` and `source_commit`.
The job YAML must declare exactly those inputs plus your binding names, `type:
command`, its `command`, and one named `experiment_outputs` output. The controller
sets compute, environment, timeout and one instance; additional job YAML infrastructure
settings are not applied. Assets use read-only mounts. Supported environment variables
are `PYTHONPATH`, `CUBLAS_WORKSPACE_CONFIG`, `PYTHONUNBUFFERED`, `PYTHONDONTWRITEBYTECODE`.

Use an explicitly versioned workspace environment backed by the approved image
digest, without build/conda overlays. Resource verification checks the GPU count
against the compute SKU and checks asset types. Sign in to Azure CLI in the
controller's OS for developer CLI use. The dashboard uses its backend-owned browser
credential; `AzureCliCredential` remains the developer fallback. Neither opens
interactive login automatically during research.

For developer CLI use, provision the pinned runtime explicitly (the dashboard's
Copilot sign-in button handles this when missing):

```bash
COPILOT_CLI_EXTRACT_DIR="$PWD/.runtime/copilot-sdk" .venv/bin/python -m copilot download-runtime
```

Use the printed executable path relative to this workspace. On Windows, set
`$env:COPILOT_CLI_EXTRACT_DIR` before the equivalent command using the Windows venv.
Authenticate the isolated runtime through its supported local mechanism, such as a
browser login with the standalone Copilot CLI. Provision that helper explicitly:

```bash
.venv/bin/python -B tools/install_copilot_cli.py
```

This downloads the official standalone CLI version matching the SDK and verifies
its release SHA-256 checksum. The hostless SDK runtime itself cannot run `login`.
The standalone CLI is used only for authentication; research uses the restricted SDK
adapter. A VS Code sign-in alone does not establish this authentication. Existing
token environment variables remain supported by the underlying adapter for automation.
The dashboard launcher removes those overrides from its own process on every start
to ensure it uses the browser account. It does not change your shell environment or
persist secrets in service settings.

Coding sessions can read explicitly reviewed UTF-8 files, replace existing files
allowed by the contract and submit a structured plan. Writes require the current
file hash. No shell, Git, arbitrary discovery or Azure tools are exposed. This is
a capability boundary, not an OS sandbox.

## Independent scoring

Live decisions use scores recomputed by the reviewed evaluator. The controller
freezes protected evaluator/split/helper files outside the editable repository and
runs them in the specified local Python with candidate imports excluded.

The evaluator accepts `--outputs`, `--request`, `--result`. The request includes
experiment/parent/commit/job identity, evaluation fingerprint, objective and
constraints. The result contains `identity` (the five identity fields) and `metrics`
(finite objective and constraint values). See the
[scoring protocol](Docs/EXPERIMENT_CONTRACT.md#live-scoring-protocol).
Provision its dependencies and reference-data access separately.

The scorer must recompute from independently validated evidence or evaluate the
model itself. Copying candidate metrics defeats independent evaluation. Treat model
files and predictions as untrusted data; do not execute candidate code or unsafe
serialized objects. Frozen hashes prove identity, not scientific validity.

## Budgets and recovery

- `max_experiments` counts candidates; `EXP-000` is separate.
- GPU admission reserves verified GPUs × full job timeout, including baseline.
- AI credits form one pool shared by all experiments. Each fresh session receives
  the remaining pool as its SDK `max_ai_credits` limit, with no fixed per-experiment
  division. Copilot currently requires at least 30 available credits to start.
- After the session finishes (or confirms abort), provider aggregate usage settles
  its reservation and returns unused credits to the pool. A proven failure before
  sending the prompt releases the reservation; unconfirmed usage remains held.
- When the remaining allowance is too small, research waits without another model
  request. **Shared AI credits** shows the minimum addition and, when usage history
  exists, an approximate estimate for remaining experiments with 25% headroom.
  **Add to allowance** records a human-approved increase without changing the
  contract, source or baseline. Use **Start loop** or **Resume loop** to continue.
  This does not purchase GitHub credits. Additions are idempotent and retained in
  the ledger. The session ceiling is soft and one response may exceed it.
- Provisioning/idle compute, cancellation delays and accounting may differ. Use
  provider spending controls too; local allowances are not invoice caps.

Submission intent, deterministic job name and source manifest are persisted before
Azure submission. A lost response is reconciled with that job, never automatically
resubmitted. An unresolved/mismatched job requires inspection. Interrupted coding
attempts are retained and never replayed. Negative results stay in history.

**Stop loop** saves a permanent stop, aborts active Copilot work, and requests
cancellation of this loop's active Azure job. The UI shows **Stopping** until Azure
confirms a terminal status; cancellation errors remain visible and can be retried.
Saved results and remote artifacts are retained. Adding credits never clears a
human stop. Closing the browser does not stop the driver. Stopping the server
interrupts the local controller; restart and
use **Resume** to reconcile pending work. Do not reset/delete the run.

Provider references: [session limits](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/set-session-limit)
and [SDK usage metrics](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/usage-and-billing).

## CLI and reports

Use `.venv/bin/python -m research_intern.main` (Windows: `.venv\Scripts\python.exe`):

```text
readiness --json
configure-live --config .runtime/live-settings.json --authorize-live
verify-services --authorize-live
baseline --authorize-live
start-live --authorize-live
resume-live --authorize-live
status-live
stop-live
report-live
```

`report-live` prints JSON; **Download report** exports the same information.
It includes outcomes, metrics, diffs, job IDs, commits, evaluation/scorer hashes and
reservations. Output/log/artifact bundles remain under
`.runtime/research-project/experiments/EXP-N/`. Reports contain evidence hashes,
not large model artifacts; preserve the entire project directory for recovery.
Offline simulations stay separately labelled under `.runtime/simulations` and
cannot become live baseline evidence.

## Development checks

```bash
.venv/bin/python -B tools/check_platform.py
.venv/bin/python -m research_intern.main run --max-experiments 3 --scenario mixed
```

The checker parses Python/JSON schemas and runs all offline tests on an exact
temporary source copy, avoiding slow WSL Git fixtures on Windows-mounted volumes.
Logs and the tested source inventory go to `.runtime/test-results`. Ordinary tests
make no model/Azure/GPU calls. They include loopback HTTP/SSE and installed provider
SDK request construction with fake clients.

## Code map

| Area | Responsibility |
|---|---|
| `live.py`, `offline.py` | Compose live or simulated runs |
| `controller/` | Candidate lifecycle, execution, recovery, mission control |
| `copilot/` | Restricted source tools and fresh SDK sessions |
| `execution/` | Azure adapter and standardized output validation |
| `evaluation/` | Independent scoring and deterministic decisions |
| `ledger/` | SQLite history, research memory and reservations |
| `workspace/` | Uploads, Git state, scope enforcement and locks |
| `api/` | Loopback API, SSE and browser UI |

Design: [scope](Docs/MVP_SCOPE.md), [architecture](Docs/ARCHITECTURE.md),
[research loop](Docs/RESEARCH_LOOP.md), [contract](Docs/EXPERIMENT_CONTRACT.md).
Read [AGENTS.md](AGENTS.md) before implementation changes.
