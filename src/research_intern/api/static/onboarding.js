"use strict";

let choicesDirty = false, targetDirty = false, discoveryKey = "", subscriptionAttempted = false;
const targetFields = ["subscription_id", "resource_group", "workspace_name", "compute"];
const resourceKinds = ["subscriptions", "resource_groups", "workspaces", "computes"];

function selectProjectFile(archive, replacement = false) {
  if (busy) return;
  const picker = $(archive ? "zip-picker" : "folder-picker");
  if (!archive && !("webkitdirectory" in picker)) { error("This browser does not support folder selection. Choose a ZIP instead."); return; }
  picker.dataset.expectedProject = replacement ? snapshot.onboarding?.project_id || "" : "";
  if (replacement && !picker.dataset.expectedProject) { error("Refresh the page before choosing another project."); return; }
  $("change-project-dialog").close();
  picker.click();
}

function resetProjectForm() {
  choicesDirty = false; targetDirty = false; discoveryKey = ""; subscriptionAttempted = false;
  dirtyBudget = false; budgetKey = null;
  $("setup-approved").checked = false;
  $("existing-experiment").dataset.edited = "";
  $("existing-jobs").replaceChildren();
  for (const kind of resourceKinds) $(kind + "-list").replaceChildren();
  for (const id of ["resource-status", "upload-status"]) $(id).textContent = "";
  if ($("experiment-dialog").open) $("experiment-dialog").close();
  selectedRun = null; selectedExperiment = null; detailRevision = null; ++detailVersion;
}

function excludedProjectFile(path) {
  const parts = path.toLowerCase().split("/"), name = parts.pop(); parts.shift();
  const skip = new Set([".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".cache", ".runtime", ".idea", ".vscode", ".azure", ".ssh", ".aws", ".copilot", ".codex"]);
  return parts.some(p => skip.has(p) || p.endsWith(".egg-info")) ||
    (name === ".env" || name.startsWith(".env.") && ![".env.example", ".env.sample", ".env.template"].includes(name)) ||
    ["id_rsa", "id_ed25519", "credentials", "credentials.json", "secrets.json", ".ds_store", "thumbs.db"].includes(name) ||
    /\.(pem|key|pfx|p12|cer|pyc|pyo|tmp|swp)$/.test(name);
}

function options(id, values, selected, multiple = false) {
  const select = $(id); select.replaceChildren();
  if (!multiple) { const empty = node("option", "Select a file"); empty.value = ""; select.append(empty); }
  for (const name of [...new Set(values)]) {
    const option = node("option", name); option.value = name;
    option.selected = multiple ? (selected || []).includes(name) : selected === name;
    select.append(option);
  }
}

function renderOnboarding(state) {
  const analysis = state?.analysis;
  $("upload-zip").hidden = !snapshot.project.upload_available;
  $("change-project").hidden = !state?.project_id;
  $("project-archive-note").hidden = !state?.previous_project_archive;
  $("project-archive-note").textContent = state?.previous_project_archive ? `Previous project preserved at ${state.previous_project_archive}` : "";
  $("inspect-project").hidden = snapshot.project.status === "missing" || Boolean(state?.configured);
  $("project-discovery").hidden = !analysis;
  $("azure-discovery").hidden = !analysis;
  if (!analysis) return;
  $("automatic-evaluation").textContent = state.evaluation_setup?.message || "Evaluation inputs will be detected from the selected project YAML.";
  const summary = state.import || {}, count = Object.values(summary.excluded || {}).reduce((a, b) => a + b, 0);
  $("discovery-summary").textContent = `${analysis.files.length} source files; ${Object.keys(summary.assets || {}).length} retained data, weight or result files; ${count} environment, cache or private files excluded. ${analysis.problems.join(" ")}`;
  $("budget-note").textContent = state.configured ? "AI credits are shared across the whole loop. Add to that allowance below; experiment and GPU limits stay fixed." : "Set the maximum GPU time, candidate experiments and shared AI-credit allowance for this research loop.";
  $("prepare-project").hidden = true;
  $("evaluation-section").hidden = !snapshot.project.workspace_prepared;
  const key = JSON.stringify([analysis.files, analysis.jobs, state.configured]);
  if (!choicesDirty || discoveryKey !== key) {
    const contract = analysis.existing_contract || {};
    options("job-config", analysis.jobs.filter(j => j.supported).map(j => j.path), state.job_config);
    options("evaluation-file", [...analysis.evaluators, ...analysis.files.filter(p => p.endsWith(".py"))], state.evaluation_file || contract.evaluation?.evaluator || (analysis.evaluators.length === 1 ? analysis.evaluators[0] : ""));
    options("validation-file", [...analysis.validation_files, ...analysis.files.filter(p => /\.(json|txt|csv)$/.test(p))], state.validation_file || contract.evaluation?.validation_split || (analysis.validation_files.length === 1 ? analysis.validation_files[0] : ""));
    const blocked = new Set([$("evaluation-file").value, $("validation-file").value, state.job_config, ...(state.evaluation_setup?.protected || [])]);
    options("editable-files", analysis.files.filter(p => !p.startsWith(".research_intern/") && !blocked.has(p)), state.editable || contract.scope?.editable || analysis.training, true);
    $("research-goal").value = state.goal || contract.goal || "";
    $("goal-metric").value = state.metric || contract.objective?.metric || "";
    $("goal-direction").value = state.direction || contract.objective?.direction || "maximize";
    $("evaluation-command").value = state.evaluation_command || "";
    $("evaluation-metrics").value = state.evaluation_metrics || "metrics.json";
    $("scoring-python").value = state.scoring_python || "";
    $("job-timeout").value = state.job_timeout_seconds || 3600;
    $("existing-compatible").checked = Boolean(state.existing_compatible);
    $("existing-output").value = state.existing_output || "";
    $("research-constraints").value = Object.entries(state.constraints || contract.constraints || {}).flatMap(([metric, bounds]) => Object.entries(bounds).map(([op, value]) => `${metric} ${op === "max" ? "<=" : ">="} ${value}`)).join("\n");
    discoveryKey = key;
  }
  if (!targetDirty) for (const field of targetFields) $("target-" + field).value = state.target?.[field] || "";
  const job = analysis.jobs.find(j => j.path === $("job-config").value);
  if (!$("existing-experiment").dataset.edited) $("existing-experiment").value = job?.experiment_name || "";
  const metrics = [...analysis.metrics, ...(state.existing_run?.metrics || [])].flatMap(m => Object.keys(m.values));
  $("metric-options").replaceChildren(...[...new Set(metrics)].map(value => { const item = node("option"); item.value = value; return item; }));
  badge($("azure-target-state"), state.target_validated && !targetDirty ? "Resources checked" : "Choose resources", state.target_validated && !targetDirty ? "good" : "");
  $("choices-status").textContent = choicesDirty ? "Unsaved project choices" : state.goal ? "Project choices saved" : "Review the detected files and enter your goal.";
  const run = state.existing_run;
  $("existing-job-status").textContent = run ? `${run.job.name}: ${run.message}` : state.baseline_choice === "new" ? "A new baseline will run after you prepare research and check connections." : "Find existing jobs, then select one or choose a new baseline.";
  $("existing-compatibility").hidden = !run;
  $("existing-job-evidence").replaceChildren(...(run?.metrics || []).map(m => node("p", `${m.path}: ${Object.entries(m.values).map(([k, v]) => `${k} = ${number(v)}`).join(", ")}`)));
  if (run?.files?.length) {
    const details = node("details"), list = node("pre", run.files.join("\n"));
    details.append(node("summary", `${run.files.length} downloaded files (reported metrics are unverified until re-scoring)`), list);
    $("existing-job-evidence").append(details);
  }
  $("prepare-status").textContent = state.configured ? "Research prepared. Establish the baseline once account checks pass." : state.scoring_environment?.message || "Preparation configures evaluation and its Python dependencies automatically. First-time downloads may take several minutes. No training job or Copilot reasoning starts during preparation.";
  if (run && state.configured && !snapshot.live?.baseline_accepted) {
    $("live-message").textContent = run.phase === "downloaded" ? "Re-score the selected run's saved artifacts to establish the baseline. No duplicate Azure job is submitted." : run.message;
    $("measure-baseline").textContent = "Re-score existing baseline";
  } else if (!state.configured) $("live-message").textContent = "Prepare research using your project and Azure choices above.";
  if (snapshot.connections?.azure.status === "connected" && !subscriptionAttempted && !state.configured) {
    subscriptionAttempted = true;
    loadResources("subscriptions").catch(exc => { $("resource-status").textContent = exc.message; });
  }
}

function onboardingControls(locked) {
  const state = snapshot.onboarding, configured = state?.configured;
  for (const id of ["upload-zip", "inspect-project"]) $(id).disabled = locked;
  for (const id of ["change-project", "choose-project-folder", "choose-project-zip"]) $(id).disabled = locked;
  for (const id of ["save-project-choices", "validate-target", "prepare-discovered"])
    $(id).disabled = locked || Boolean(configured);
  document.querySelectorAll(".resource-load").forEach(button => { button.disabled = locked || Boolean(configured) || snapshot.connections?.azure.status !== "connected"; });
  document.querySelectorAll("#project-choices input, #project-choices select, #project-choices textarea, #azure-discovery input").forEach(field => { field.disabled = locked || Boolean(configured); });
  for (const id of ["find-existing-jobs", "select-existing-job", "choose-new-baseline", "existing-compatible", "existing-output", "existing-experiment", "existing-job-id"])
    $(id).disabled = locked || Boolean(snapshot.live?.initialized);
  $("prepare-discovered").disabled ||= !$("setup-approved").checked;
  if (state?.existing_run && state.existing_run.phase !== "downloaded") $("measure-baseline").disabled = true;
}

function projectChoices() {
  const constraints = {};
  for (const line of $("research-constraints").value.split("\n").filter(line => line.trim())) {
    const match = line.trim().match(/^(.+?)\s*(<=|>=)\s*(-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)$/i);
    if (!match || !Number.isFinite(Number(match[3]))) throw new Error("Write each constraint like latency_ms <= 100 or recall >= 0.8.");
    (constraints[match[1].trim()] ||= {})[match[2] === "<=" ? "max" : "min"] = Number(match[3]);
  }
  return {goal: $("research-goal").value.trim(), job_config: $("job-config").value,
    metric: $("goal-metric").value.trim(), direction: $("goal-direction").value,
    evaluation_file: $("evaluation-file").value, validation_file: $("validation-file").value,
    evaluation_command: $("evaluation-command").value.trim(), evaluation_metrics: $("evaluation-metrics").value.trim(),
    editable: Array.from($("editable-files").selectedOptions).map(o => o.value), constraints,
    budget: {max_experiments: Number($("experiment-limit").value), max_gpu_hours: Number($("gpu-hours").value), max_ai_credits: Number($("ai-credits").value)},
    scoring_python: $("scoring-python").value.trim(), job_timeout_seconds: Number($("job-timeout").value),
    existing_compatible: $("existing-compatible").checked, existing_output: $("existing-output").value.trim()};
}

async function saveProjectChoices() {
  await api("/api/onboarding/choices", projectChoices()); choicesDirty = false; dirtyBudget = false;
}

function targetValues() { return Object.fromEntries(targetFields.map(field => [field, $("target-" + field).value.trim()])); }

async function onboardingAction(callback) {
  if (busy) return;
  busy = true; controls(); error();
  try { await callback(); await refresh(); } catch (exc) { error(exc.message); }
  finally { busy = false; controls(); }
}

async function loadResources(kind) {
  const selection = targetValues(), result = await api(`/api/onboarding/azure/${kind}`, selection);
  if (JSON.stringify(selection) !== JSON.stringify(targetValues())) return;
  $(kind + "-list").replaceChildren(...result.items.map(item => { const option = node("option", `${item.label}${item.location ? " · " + item.location : ""}`); option.value = item.value; return option; }));
  $("resource-status").textContent = result.message + (result.truncated ? " Results were limited; enter an exact value if yours is missing." : "");
}

async function findExistingJobs() {
  if (targetDirty) throw new Error("Validate the changed Azure selection before searching jobs.");
  const result = await api("/api/onboarding/jobs", {experiment_name: $("existing-experiment").value.trim(), job_name: $("existing-job-id").value.trim()});
  const select = $("existing-jobs"); select.replaceChildren(node("option", "Select the matching run")); select.firstChild.value = "";
  for (const job of result.items) { const option = node("option", `${job.name} · ${job.status} · ${job.compute} · ${job.created_at || ""}`); option.value = job.name; select.append(option); }
  $("resource-status").textContent = `${result.items.length} jobs found. Select the exact run for this source, data and evaluation.${result.truncated ? " Search by run ID if it is missing." : ""}`;
}

function bindOnboarding() {
  $("inspect-project").addEventListener("click", () => onboardingAction(() => api("/api/onboarding/inspect", {})));
  $("upload-zip").addEventListener("click", () => selectProjectFile(true));
  $("change-project").addEventListener("click", () => $("change-project-dialog").showModal());
  $("close-project-picker").addEventListener("click", () => $("change-project-dialog").close());
  $("choose-project-folder").addEventListener("click", () => selectProjectFile(false, true));
  $("choose-project-zip").addEventListener("click", () => selectProjectFile(true, true));
  $("zip-picker").addEventListener("change", event => {
    const file = event.target.files[0]; event.target.value = "";
    if (!file) return;
    if (file.size > 2 * 1024 ** 3) { error("Select a ZIP up to 2 GiB."); return; }
    onboardingAction(() => uploadFolder([file], true, event.target.dataset.expectedProject || ""));
  });
  $("project-choices").addEventListener("input", () => { choicesDirty = true; $("choices-status").textContent = "Unsaved project choices"; });
  $("project-choices").addEventListener("submit", event => { event.preventDefault(); onboardingAction(saveProjectChoices); });
  $("setup-approved").addEventListener("change", controls);
  for (const id of ["existing-compatible", "existing-output"]) $(id).addEventListener("input", () => { choicesDirty = true; });
  $("existing-experiment").addEventListener("input", () => { $("existing-experiment").dataset.edited = "true"; });
  document.querySelectorAll(".resource-load").forEach(button => button.addEventListener("click", () => onboardingAction(() => loadResources(button.dataset.kind))));
  targetFields.forEach((field, index) => $("target-" + field).addEventListener("input", () => {
    targetDirty = true;
    for (let i = index + 1; i < targetFields.length; i++) { $("target-" + targetFields[i]).value = ""; $(resourceKinds[i] + "-list").replaceChildren(); }
    badge($("azure-target-state"), "Validate changed selection");
  }));
  targetFields.slice(0, -1).forEach((field, index) => $("target-" + field).addEventListener("change", () => {
    if ($("target-" + field).value.trim() && snapshot.connections?.azure.status === "connected")
      loadResources(resourceKinds[index + 1]).catch(exc => { $("resource-status").textContent = exc.message; });
  }));
  $("validate-target").addEventListener("click", () => onboardingAction(async () => {
    await api("/api/onboarding/target", targetValues()); targetDirty = false;
    $("resource-status").textContent = "Workspace and compute access checked.";
    await findExistingJobs();
  }));
  $("find-existing-jobs").addEventListener("click", () => onboardingAction(findExistingJobs));
  $("select-existing-job").addEventListener("click", () => onboardingAction(async () => {
    if (!$("existing-jobs").value) throw new Error("Select an Azure job from the results.");
    await api("/api/onboarding/job", {job_name: $("existing-jobs").value});
    $("existing-compatible").checked = false;
  }));
  $("choose-new-baseline").addEventListener("click", () => onboardingAction(() => api("/api/onboarding/new-baseline", {authorized: true})));
  $("prepare-discovered").addEventListener("click", () => onboardingAction(async () => {
    if (!$("setup-approved").checked) throw new Error("Review the displayed setup and select its confirmation checkbox.");
    if (targetDirty) throw new Error("Validate the changed Azure target before preparation.");
    await saveProjectChoices();
    $("prepare-status").textContent = "Preparing evaluation inputs and Python dependencies. First-time downloads may take several minutes…";
    await api("/api/onboarding/prepare", {authorized: true});
    await api("/api/project/live/verify", {});
  }));
}
