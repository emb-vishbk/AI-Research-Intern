"use strict";

const $ = id => document.getElementById(id);
const number = value => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: 4}) : "—";
const friendly = value => (value || "Not started").toLowerCase().replaceAll("_", " ").replaceAll("-", " ");
let snapshot = null, busy = false, online = false, dirtyBudget = false, budgetKey = null;
let stream = null, detailVersion = 0, selectedExperiment = null, selectedRun = null, detailRevision = null;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function error(message = "") { $("error").textContent = message; $("error").hidden = !message; }
function badge(element, text, kind = "") { element.textContent = text; element.className = `badge ${kind}`; }
function decisionKind(value) { return ["KEEP", "GOAL_REACHED"].includes(value) ? "good" : value === "FAILED" ? "bad" : value === "REJECT" ? "warn" : ""; }
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Research-Intern": "1"}, body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check the entered values and try again.");
  return result;
}
async function refresh() { render(await api("/api/workspace")); }
function controls() {
  if (!snapshot) return;
  const run = snapshot.run, project = snapshot.project;
  const active = Boolean(run?.driver.active), locked = busy || active || snapshot.changing || !online;
  $("upload").disabled = locked || !project.upload_available;
  $("prepare-project").disabled = locked || !project.prepare_available;
  $("confirm-evaluation").disabled = locked || !project.workspace_prepared || project.evaluation_status !== "unconfirmed";
  $("budget-fields").disabled = locked || project.status !== "contract_valid";
  $("save-budget").disabled = !dirtyBudget && project.budget_saved;
  $("start").disabled = locked || dirtyBudget || !project.budget_saved || !snapshot.can_start;
  const pending = run?.experiments.some(r => ["SUBMITTING", "SUBMITTED", "RUNNING"].includes(r.state));
  const interrupted = run && (["INTERRUPTED", "RECOVERY_REQUIRED", "RUNNING", "PAUSED"].includes(run.controller.state) || run.driver.error);
  const humanStop = Boolean(run?.research_state.blocking_reasons.includes("HUMAN_STOP"));
  const canResume = Boolean(run && !active && (pending || (interrupted && !humanStop)));
  $("resume").hidden = !canResume;
  $("resume").disabled = locked;
  $("resume").textContent = pending ? "Resume collection" : "Resume saved loop";
  $("stop").hidden = !(active || canResume);
  $("stop").disabled = busy || !online || Boolean(run?.research_state.blocking_reasons.includes("HUMAN_STOP"));
}
function render(data) {
  snapshot = data;
  const project = data.project, run = data.run;
  $("project-name").textContent = project.status === "missing" ? "Your next research project" : project.name;
  $("project-message").textContent = project.message;
  badge($("project-state"), project.workspace_prepared ? "Workspace prepared" : {missing: "Not loaded", needs_contract: "Needs contract", contract_valid: "Source checked", needs_attention: "Needs attention"}[project.status], project.status === "contract_valid" ? "good" : ["needs_attention", "needs_contract"].includes(project.status) ? "warn" : "");
  $("upload").hidden = !project.upload_available;
  $("upload-note").hidden = !project.upload_available;
  $("objective").hidden = !project.objective;
  if (project.objective) $("objective").textContent = `${project.objective.direction === "maximize" ? "Maximize" : "Minimize"} ${project.objective.metric}`;
  $("prepare-project").hidden = !project.prepare_available;
  $("source-version").hidden = !project.source_commit;
  $("source-version").textContent = project.source_commit ? `Source version: ${project.source_commit}. Setup only; EXP-000 has not been imported.` : "";
  renderEvaluation(project);
  const nextKey = JSON.stringify([project.contract_sha256, project.budget]);
  if (!dirtyBudget || (budgetKey !== null && project.contract_sha256 !== JSON.parse(budgetKey)[0])) {
    $("gpu-hours").value = project.budget?.max_gpu_hours ?? "";
    $("experiment-limit").value = project.budget?.max_experiments ?? "";
    $("ai-credits").value = project.budget?.max_ai_credits ?? "";
    dirtyBudget = false;
    budgetKey = nextKey;
  }
  $("budget-status").textContent = dirtyBudget ? "Unsaved changes" : project.budget_saved ? "Draft limits saved" : "Set before starting";
  $("budget-note").textContent = project.status !== "contract_valid" ? "A valid project contract is required." : "Draft limits only. Live compute and credit limits are not enforced yet.";
  $("usage").textContent = "Compute and credit usage: unavailable until live metering is connected.";
  const readiness = data.readiness;
  $("readiness").textContent = project.status === "missing" ? "Upload a project to begin setup. Live research execution is not connected yet." : project.status === "needs_attention" ? "Resolve the project issue above before starting." : readiness ? `Live execution is blocked by ${readiness.blockers.length} checks. Expand the checklist below for details.` : data.start_blocker;
  $("readiness-details").hidden = !readiness;
  $("readiness-checks").replaceChildren(...(readiness?.checks || []).map(check => {
    const item = node("li"), label = node("span");
    badge(label, check.ready ? "Checked" : "Blocked", check.ready ? "good" : "warn");
    item.append(label, node("span", check.message));
    return item;
  }));
  if (run) renderRun(run);
  else renderEmpty();
  if (data.history_error) $("activity-message").textContent = `Saved loop needs attention: ${data.history_error}`;
  if ($("experiment-dialog").open) {
    if (!run || run.id !== selectedRun) { $("experiment-dialog").close(); ++detailVersion; }
    else if (detailRevision !== run.research_state.ledger_revision) loadDetail(selectedExperiment);
  }
  controls();
}
function renderEvaluation(project) {
  $("evaluation-section").hidden = project.status === "missing";
  const protocol = project.evaluation, confirmed = project.evaluation_status === "confirmed";
  badge($("evaluation-state"), confirmed ? "Confirmed" : "Not confirmed", confirmed ? "good" : "warn");
  $("evaluation-note").textContent = confirmed
    ? "Your evaluation policy is confirmed for this source version. This does not verify a measured baseline or enable live runs."
    : !protocol ? "The metric and evaluation procedure still need researcher agreement. Add an evaluation section to the contract with the metric definition, dataset version, evaluator and fixed validation split before confirming."
    : !project.workspace_prepared ? "Review this procedure and its constraints, then prepare the local workspace before confirming."
    : "Confirm only after reviewing the metric definition, data split, procedure and constraints with the researcher.";
  $("evaluation-details").hidden = !protocol;
  $("confirm-evaluation").hidden = !protocol || confirmed || !project.workspace_prepared;
  const policy = $("evaluation-policy"); policy.replaceChildren();
  if (!protocol) return;
  const constraints = (project.objective?.constraints || []).map(c => `${c.metric}: ${c.operator} ${c.value}`).join("; ") || "None declared";
  for (const [label, value] of [
    ["Objective", `${project.objective.metric} · ${project.objective.direction}`],
    ["Target", project.objective.target ?? "No target declared"],
    ["Metric definition and scale", protocol.metric_definition], ["Evaluation procedure", protocol.procedure],
    ["Dataset version", protocol.dataset_version], ["Fixed validation split", protocol.validation_split],
    ["Split SHA-256", protocol.validation_split_sha256], ["Evaluator", protocol.evaluator],
    ["Evaluator SHA-256", protocol.evaluator_sha256], ["Constraints", constraints],
    ["Editable paths", project.scope.editable.join(", ")], ["Protected paths", project.scope.protected.join(", ")],
    ["Evaluation fingerprint", project.evaluation_fingerprint]
  ]) policy.append(node("dt", label), node("dd", String(value)));
}
function renderEmpty() {
  badge($("loop-state"), "Not started");
  $("activity-title-text").textContent = "No experiment running";
  $("activity-message").textContent = "The loop will prepare a candidate, run it on Azure, and evaluate the result.";
  $("hypothesis").hidden = true;
  $("history-kind").hidden = true;
  $("history-note").hidden = true;
  $("experiment-count").textContent = "No experiments yet";
  for (const id of ["baseline", "best", "improvement"]) { $(id).textContent = "—"; $(id).className = ""; }
  $("experiments").replaceChildren();
  $("empty-results").hidden = false;
  document.querySelectorAll("#stages li").forEach(item => { item.className = ""; item.removeAttribute("aria-current"); });
}
function changeText(score, reference, direction) {
  if (!Number.isFinite(score) || !Number.isFinite(reference)) return "—";
  const delta = (score - reference) * (direction === "minimize" ? -1 : 1);
  return `${delta > 0 ? "+" : ""}${number(delta)}`;
}
function renderRun(run) {
  const state = run.research_state, last = state.last_experiment;
  const active = run.driver.active, stopped = state.blocking_reasons.includes("HUMAN_STOP");
  badge($("loop-state"), stopped ? (active ? "Stopping" : "Stopped") : active ? "Running" : friendly(run.controller.state), active ? "active" : "");
  $("activity-title-text").textContent = `${last?.experiment_id || "Research loop"} · ${friendly(last?.state)}`;
  $("activity-message").textContent = stopped ? "Stop recorded. No further experiments will start; submitted work can still be collected." : run.driver.error || run.controller.message || "The controller follows the saved objective and experiment limit.";
  $("hypothesis").hidden = !run.current_plan?.hypothesis;
  $("hypothesis").textContent = run.current_plan?.hypothesis || "";
  const preparation = run.last_preparation?.stage;
  if (active && ["PREPARING", "EDITING", "COMMITTING"].includes(preparation)) $("activity-title-text").textContent = "Preparing the next experiment";
  let stage = ({PREPARED: "validating", SUBMITTING: "queued", SUBMITTED: "queued", RUNNING: "training", COLLECTED: "evaluating", RECORDED: "recording"})[last?.state];
  if (["PREPARING", "EDITING"].includes(preparation)) stage = "preparing";
  if (preparation === "COMMITTING") stage = "validating";
  let passed = true;
  document.querySelectorAll("#stages li").forEach(item => {
    const current = active && item.dataset.stage === stage;
    if (current) passed = false;
    item.className = current ? "current" : active && stage && passed ? "done" : "";
    if (current) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
  });
  $("history-kind").hidden = false;
  $("history-kind").textContent = active ? "Active simulation" : "Saved simulation";
  $("history-note").hidden = false;
  $("history-note").textContent = `Synthetic ${state.objective.metric} scores from an offline loop. These are not measurements of your ML project.`;
  $("baseline").textContent = number(state.baseline?.score);
  $("best").textContent = number(state.best_experiment?.score);
  const improvement = changeText(state.best_experiment?.score, state.baseline?.score, state.objective.direction);
  $("improvement").textContent = improvement;
  $("improvement").className = improvement.startsWith("+") ? "positive" : "";
  $("experiment-count").textContent = `${state.budget.allocated_experiments} / ${state.budget.max_experiments} experiments`;
  $("empty-results").hidden = Boolean(run.experiments.length);
  const rows = run.experiments.map(record => {
    const row = node("tr"), id = node("td"), button = node("button", record.experiment_id);
    button.dataset.experiment = record.experiment_id;
    button.setAttribute("aria-haspopup", "dialog");
    id.append(button);
    const decision = node("td"), pill = node("span");
    badge(pill, record.decision || friendly(record.state), decisionKind(record.decision));
    decision.append(pill);
    row.append(id, node("td", record.parent_experiment ? record.planned_intervention : "Baseline"), node("td", number(record.score)), decision);
    return row;
  });
  $("experiments").replaceChildren(...rows);
}
async function loadDetail(experimentId) {
  const run = snapshot?.run;
  if (!run) return;
  selectedExperiment = experimentId; selectedRun = run.id;
  detailRevision = run.research_state.ledger_revision;
  const version = ++detailVersion;
  $("detail-title").textContent = experimentId;
  $("detail-content").replaceChildren(node("p", "Loading evidence…"));
  if (!$("experiment-dialog").open) $("experiment-dialog").showModal();
  try {
    const record = await api(`/api/runs/${encodeURIComponent(run.id)}/experiments/${encodeURIComponent(experimentId)}`);
    if (version !== detailVersion || !$("experiment-dialog").open) return;
    const content = $("detail-content"); content.replaceChildren();
    const summary = node("div", undefined, "detail-summary"), pill = node("span");
    badge(pill, record.decision || friendly(record.state), decisionKind(record.decision));
    summary.append(pill, node("span", `Parent: ${record.parent_experiment || "None · baseline"}`)); content.append(summary);
    const direction = run.research_state.objective.direction;
    const parent = run.experiments.find(r => r.experiment_id === record.parent_experiment);
    const score = record.evaluation?.score;
    const grid = node("div", undefined, "detail-grid");
    for (const [label, value] of [[run.research_state.objective.metric, number(score)], ["Improvement vs parent", changeText(score, parent?.score, direction)], ["Improvement vs baseline", changeText(score, run.research_state.baseline?.score, direction)]]) {
      const cell = node("div"); cell.append(node("span", label), node("strong", value)); grid.append(cell);
    }
    content.append(grid);
    const section = (title, text, className) => { content.append(node("h3", title), node("p", text || "Not recorded.", className)); };
    section("Hypothesis", record.hypothesis);
    if (record.candidate?.plan) { section("Observation", record.candidate.plan.observation); section("Diagnosis", record.candidate.plan.diagnosis); }
    section("Changes made", record.planned_intervention);
    if (record.candidate?.plan) section("Expected effect", record.candidate.plan.expected_effect);
    section("Outcome", record.evaluation?.conclusion || "Evaluation has not completed.");
    if (record.evaluation) section("Best experiment", run.research_state.best_experiment?.experiment_id === experimentId ? "This is the current best experiment." : record.evaluation.new_best ? "This became the best when evaluated; a later experiment has since improved it." : "This experiment did not replace the best result.");
    if (record.failure_type) section(`Failure · ${friendly(record.failure_type)}`, record.failure_message, "detail-failure");
    for (const constraint of record.evaluation?.constraint_results || []) section(`Constraint · ${constraint.satisfied ? "passed" : "failed"}`, `${constraint.metric}: ${number(constraint.actual)} (${constraint.operator} ${number(constraint.threshold)})`);
    const info = node("dl");
    for (const [label, value] of [["Exact commit", record.git_commit], ["Simulated job", record.job_id || "Not submitted"], ["Outputs", record.outputs_path || "Not collected"]]) info.append(node("dt", label), node("dd", value));
    content.append(info);
    for (const [label, value] of [["Code diff", record.diff || "Baseline: no candidate diff."], ["All metrics", JSON.stringify(record.metrics || {}, null, 2)], ["Validation checks", JSON.stringify(record.candidate?.validation || {}, null, 2)]]) {
      const details = node("details"); details.append(node("summary", label), node("pre", value)); content.append(details);
    }
  } catch (exc) { if (version === detailVersion) $("detail-content").replaceChildren(node("p", exc.message, "detail-failure")); }
}
function uploadFolder(files) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const file of files) form.append("files", file, file.webkitRelativePath);
    const request = new XMLHttpRequest();
    request.open("POST", "/api/project/upload"); request.setRequestHeader("X-Research-Intern", "1");
    request.timeout = 180000;
    request.upload.onprogress = event => { $("upload-status").textContent = event.lengthComputable ? `Uploading ${Math.round(event.loaded / event.total * 100)}%…` : "Uploading source files…"; };
    request.onload = () => {
      let result;
      try { result = JSON.parse(request.responseText); } catch { reject(new Error("The server returned an invalid upload response.")); return; }
      if (request.status >= 200 && request.status < 300) resolve(result);
      else reject(new Error(typeof result.detail === "string" ? result.detail : "The folder could not be uploaded."));
    };
    request.onerror = () => reject(new Error("Upload connection lost. The project status will be checked again."));
    request.ontimeout = () => reject(new Error("Upload timed out. The project status will be checked again."));
    request.send(form);
  });
}
$("upload").addEventListener("click", () => {
  if (!("webkitdirectory" in $("folder-picker"))) { error("This browser does not support folder selection. Use a current desktop browser."); return; }
  $("folder-picker").click();
});
$("folder-picker").addEventListener("change", async event => {
  // The controller creates independent Git metadata; never upload local history,
  // hooks or remote configuration from the researcher's original repository.
  const files = Array.from(event.target.files).filter(file =>
    !file.webkitRelativePath.split("/").slice(1).some(part => part.toLowerCase() === ".git"));
  event.target.value = "";
  if (!files.length || busy) return;
  error();
  if (files.length > 2000 || files.reduce((sum, file) => sum + file.size, 0) > 64 * 1024 * 1024) { error("Select a source folder up to 64 MiB and 2,000 files. Keep datasets, weights and environments separate."); return; }
  if (files.some(file => !file.webkitRelativePath)) { error("Select one folder with its relative file paths."); return; }
  busy = true; controls(); $("upload-status").textContent = "Uploading source files…";
  try { await uploadFolder(files); $("upload-status").textContent = "Folder copied. Review its setup status before preparing."; dirtyBudget = false; }
  catch (exc) { error(exc.message); $("upload-status").textContent = ""; }
  finally { try { await refresh(); } catch (exc) { error(exc.message); } busy = false; controls(); }
});
$("budget-form").addEventListener("input", () => { dirtyBudget = true; $("budget-status").textContent = "Unsaved changes"; controls(); });
async function setupAction(confirmEvaluation) {
  if (busy || !snapshot) return;
  busy = true; controls(); error();
  const project = snapshot.project;
  const body = {contract_sha256: project.contract_sha256};
  if (confirmEvaluation) Object.assign(body, {source_commit: project.source_commit,
    evaluation_fingerprint: project.evaluation_fingerprint, confirmed: true});
  try {
    await api(confirmEvaluation ? "/api/project/evaluation/confirm" : "/api/project/prepare", body);
    await refresh();
  } catch (exc) { error(exc.message); }
  finally { busy = false; controls(); }
}
$("prepare-project").addEventListener("click", () => setupAction(false));
$("confirm-evaluation").addEventListener("click", () => setupAction(true));
$("budget-form").addEventListener("submit", async event => {
  event.preventDefault(); if (busy || !snapshot) return;
  busy = true; controls(); error();
  try {
    await api("/api/project/budget", {max_experiments: Number($("experiment-limit").value), max_gpu_hours: Number($("gpu-hours").value), max_ai_credits: Number($("ai-credits").value), contract_sha256: snapshot.project.contract_sha256});
    dirtyBudget = false; await refresh();
  } catch (exc) { error(exc.message); }
  finally { busy = false; controls(); }
});
async function action(kind) {
  if (busy || !snapshot) return;
  const run = snapshot.run;
  if (kind !== "start" && !run) return;
  busy = true; controls(); error();
  try { await api(kind === "start" ? "/api/project/start" : `/api/runs/${encodeURIComponent(run.id)}/${kind}`, {}); await refresh(); }
  catch (exc) { error(exc.message); }
  finally { busy = false; controls(); }
}
$("start").addEventListener("click", () => action("start"));
$("stop").addEventListener("click", () => action("stop"));
$("resume").addEventListener("click", () => action("resume"));
$("experiments").addEventListener("click", event => { const button = event.target.closest("button[data-experiment]"); if (button) loadDetail(button.dataset.experiment); });
$("close-detail").addEventListener("click", () => $("experiment-dialog").close());
$("experiment-dialog").addEventListener("close", () => { ++detailVersion; });
function connect() {
  if (stream) stream.close();
  stream = new EventSource("/api/workspace/events");
  stream.onopen = () => { online = true; $("connection").textContent = "Connected locally"; $("connection").classList.add("connected"); controls(); };
  stream.onerror = () => { online = false; $("connection").textContent = "Reconnecting…"; $("connection").classList.remove("connected"); controls(); };
  stream.addEventListener("snapshot", event => render(JSON.parse(event.data)));
}
window.addEventListener("pagehide", () => stream?.close());
window.addEventListener("pageshow", event => { if (event.persisted) connect(); });
refresh().then(connect).catch(exc => { error(exc.message); $("connection").textContent = "Server unavailable"; connect(); });
