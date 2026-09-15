"use strict";

const $ = id => document.getElementById(id);
let selectedRun = null, selectedExperiment = null, snapshot = null, stream = null;
let busy = false, activeRun = null, selectionVersion = 0, detailVersion = 0, detailKey = "";
const friendly = value => (value || "Ready").toLowerCase().replaceAll("_", " ").replaceAll("-", " ");
const number = value => Number.isFinite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: 4}) : "—";

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function error(message = "") { $("error").hidden = !message; $("error").textContent = message; }
function badge(element, value) {
  element.textContent = friendly(value);
  element.className = "pill" + (["KEEP", "GOAL_REACHED", "RUNNING"].includes(value) ? " good" :
    ["FAILED", "RECOVERY_REQUIRED"].includes(value) ? " bad" : ["REJECT", "INTERRUPTED"].includes(value) ? " warn" : "");
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json", "X-Research-Intern": "1"}, body: JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check the requested values and try again.");
  return result;
}
async function checkProject() {
  $("check-project").disabled = true;
  try {
    const project = await api("/api/project");
    $("project-state").textContent = {missing: "Awaiting repository", contract_valid: "Contract parsed", needs_attention: "Needs attention"}[project.status];
    $("project-message").textContent = project.message;
    $("project-path").textContent = project.relative_path + "/";
    $("project-path").title = project.path;
  } catch (exc) { error(exc.message); }
  finally { $("check-project").disabled = false; }
}
function controls() {
  $("new-run").disabled = busy || Boolean(activeRun);
  $("first-run").disabled = busy || Boolean(activeRun);
  if (!snapshot) return;
  const reasons = snapshot.research_state.blocking_reasons;
  const pending = snapshot.experiments.some(r => !["RECORDED", "FAILED"].includes(r.state));
  const finished = snapshot.controller.state === "STOPPED" && !snapshot.driver.error;
  const collecting = pending && snapshot.experiments.some(r => ["SUBMITTING", "SUBMITTED", "RUNNING"].includes(r.state));
  $("resume").disabled = busy || Boolean(activeRun) || (finished && !collecting);
  $("stop").disabled = busy || reasons.includes("HUMAN_STOP") || (finished && !pending);
  $("resume").textContent = collecting ? "Resume collection" : snapshot.experiments.length === 1 && snapshot.preparation_budget.used === 0 ? "Start run" : "Resume run";
}
async function refreshRuns(autoSelect = false) {
  const result = await api("/api/runs");
  activeRun = result.active_run_id;
  const list = $("run-list");
  list.replaceChildren();
  for (const run of result.runs) {
    const button = node("button", undefined, "run-link");
    button.dataset.run = run.id;
    button.setAttribute("aria-current", String(run.id === selectedRun));
    button.append(node("strong", run.id), node("small", run.error ? "Needs attention" :
      `${run.driver.active ? "Running" : friendly(run.controller.state)} · ${run.research_state.budget.allocated_experiments} experiments`));
    list.append(button);
  }
  if (!result.runs.length) list.append(node("p", "No runs yet. Create your first experiment journey.", "muted"));
  if (result.truncated) list.append(node("p", "Showing the 50 most recent runs. Older runs remain on disk.", "muted"));
  controls();
  if (autoSelect && !selectedRun) {
    const query = new URL(location.href).searchParams.get("run");
    const initial = result.runs.find(r => r.id === query) || result.runs.find(r => !r.error);
    if (initial) await selectRun(initial.id);
    else { $("empty").hidden = false; $("connection").textContent = "Local server connected"; }
  }
}
async function selectRun(runId) {
  const version = ++selectionVersion;
  if (stream) stream.close();
  stream = null;
  selectedRun = runId;
  selectedExperiment = null;
  detailKey = "";
  snapshot = null;
  ++detailVersion;
  $("workspace").hidden = true;
  $("empty").hidden = true;
  $("connection").textContent = "Loading run…";
  error();
  try {
    const data = await api(`/api/runs/${encodeURIComponent(runId)}`);
    if (version !== selectionVersion) return;
    const url = new URL(location.href); url.searchParams.set("run", runId); history.replaceState(null, "", url);
    render(data);
    connect(runId, version);
    await refreshRuns();
  } catch (exc) { if (version === selectionVersion) { error(exc.message); $("connection").textContent = "Run unavailable"; } }
}
function connect(runId, version) {
  const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
  stream = source;
  source.onopen = () => { if (version === selectionVersion) $("connection").textContent = "Live updates connected"; };
  source.onerror = () => { if (version === selectionVersion) $("connection").textContent = "Reconnecting to local server…"; };
  source.addEventListener("unavailable", event => {
    if (version !== selectionVersion) return;
    source.close(); $("connection").textContent = "Run unavailable"; error(JSON.parse(event.data).detail);
  });
  source.addEventListener("snapshot", event => {
    if (version !== selectionVersion) return;
    const data = JSON.parse(event.data);
    const wasActive = snapshot?.driver.active;
    render(data);
    if (wasActive !== data.driver.active) refreshRuns().catch(exc => error(exc.message));
  });
}
function render(data) {
  snapshot = data;
  activeRun = data.driver.active_run_id;
  const state = data.research_state, objective = state.objective, budget = state.budget;
  $("empty").hidden = true; $("workspace").hidden = false;
  $("run-label").textContent = `${data.id} · ${friendly(data.policy.scenario)}`;
  $("objective").textContent = `${objective.direction === "maximize" ? "Maximize" : "Minimize"} ${objective.metric}`;
  $("objective-detail").textContent = `Target ${number(objective.target)} · ${objective.constraints.length} hard constraint${objective.constraints.length === 1 ? "" : "s"} · synthetic evidence`;
  badge($("status"), data.driver.active ? "RUNNING" : data.controller.state === "RUNNING" ? "NO_WEB_DRIVER" : data.controller.state);
  let message = data.driver.active ? "Controller active. New candidates follow the evaluator's decisions and the fixed budget." :
    data.controller.message || "The baseline is recorded. Start the controller when you are ready.";
  if (!data.driver.active && data.controller.state === "RUNNING") message = "No driver is active in this server. Resume to reconcile persisted state; the per-run lock prevents duplicate drivers.";
  if (state.blocking_reasons.includes("HUMAN_STOP")) message = "Human stop recorded. No new candidates will run. An already submitted job can still be collected; this stop cannot be cleared.";
  $("run-message").textContent = data.driver.error || message;
  $("baseline").textContent = number(state.baseline?.score);
  $("best").textContent = number(state.best_experiment?.score);
  const delta = (state.best_experiment?.score ?? 0) - (state.baseline?.score ?? 0);
  $("best-label").textContent = `${state.best_experiment?.experiment_id || "—"} · ${delta >= 0 ? "+" : ""}${number(delta)} from baseline`;
  $("budget").textContent = `${budget.allocated_experiments} / ${budget.max_experiments}`;
  $("attempts").textContent = `${data.preparation_budget.used} / ${data.preparation_budget.limit} preparation attempts`;
  $("latest").textContent = state.last_experiment?.experiment_id || "—";
  $("latest-label").textContent = friendly(state.last_experiment?.decision || state.last_experiment?.state);
  $("chart-metric").textContent = objective.metric;
  $("hypothesis").textContent = data.current_plan?.hypothesis || "Waiting for a candidate";
  $("intervention").textContent = data.current_plan?.planned_intervention || "The controller will select a parent from recorded evidence.";
  $("preparation-stage").textContent = data.last_preparation ? `Attempt ${data.last_preparation.sequence} · ${friendly(data.last_preparation.stage)}` : "No preparation yet";
  $("selected-parent").textContent = `Parent: ${data.current_plan?.parent_experiment || state.best_experiment?.experiment_id || "—"}`;
  $("run-path").textContent = data.run_directory;
  if (!selectedExperiment || !data.experiments.some(r => r.experiment_id === selectedExperiment)) selectedExperiment = state.last_experiment.experiment_id;
  renderHistory(data.experiments);
  renderChart(data.experiments, objective);
  renderEvents(data.events, data.preparation_failures);
  const record = data.experiments.find(r => r.experiment_id === selectedExperiment);
  const key = JSON.stringify([data.id, record, data.last_preparation?.stage]);
  if (key !== detailKey) { detailKey = key; showDetail(selectedExperiment).catch(exc => error(exc.message)); }
  controls();
}
function renderHistory(records) {
  const body = $("experiments"); body.replaceChildren();
  for (const record of records) {
    const row = node("tr", undefined, record.experiment_id === selectedExperiment ? "selected" : "");
    const name = node("td"), button = node("button", record.experiment_id);
    button.dataset.experiment = record.experiment_id;
    button.setAttribute("aria-pressed", String(record.experiment_id === selectedExperiment)); name.append(button);
    const decision = node("td"), pill = node("span"); badge(pill, record.decision || record.state); decision.append(pill);
    const commit = node("td"); commit.append(node("code", record.git_commit.slice(0, 8)));
    row.append(name, node("td", record.parent_experiment || "Baseline"), node("td", number(record.score)), decision, commit);
    body.append(row);
  }
}
function renderChart(records, objective) {
  const root = $("chart"), ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg"); svg.setAttribute("viewBox", "0 0 560 182");
  const add = (tag, attrs, text) => { const item = document.createElementNS(ns, tag); for (const [k,v] of Object.entries(attrs)) item.setAttribute(k, v); if (text !== undefined) item.textContent = text; svg.append(item); return item; };
  const values = records.map(r => r.score).filter(Number.isFinite);
  if (Number.isFinite(objective.target)) values.push(objective.target);
  if (!values.length) { root.replaceChildren(node("p", "No recorded scores yet.", "muted")); return; }
  let low = Math.min(...values), high = Math.max(...values); const pad = Math.max((high - low) * .2, .005); low -= pad; high += pad;
  const x = i => 45 + i / Math.max(records.length - 1, 1) * 490, y = score => 142 - (score - low) / (high - low) * 125;
  for (let i=0;i<4;i++) { const score = low + i/3*(high-low), line = y(score); add("line", {x1:45,y1:line,x2:535,y2:line,stroke:"#e6ede9"}); add("text", {x:34,y:line+3,"text-anchor":"end",fill:"#73877e","font-size":9}, number(score)); }
  if (Number.isFinite(objective.target)) add("line", {x1:45,y1:y(objective.target),x2:535,y2:y(objective.target),stroke:"#8da89c","stroke-dasharray":"4 4"});
  let segment = [];
  const flush = () => { if(segment.length > 1) add("polyline", {points:segment.join(" "),fill:"none",stroke:"#087c68","stroke-width":2}); segment=[]; };
  records.forEach((r,i) => { if(Number.isFinite(r.score)) segment.push(`${x(i)},${y(r.score)}`); else flush(); }); flush();
  records.forEach((record,i) => {
    if(Number.isFinite(record.score)) { const point = add("circle", {cx:x(i),cy:y(record.score),r:4,fill:record.decision==="REJECT"?"#b88643":"#087c68",stroke:"white","stroke-width":2}); const title = document.createElementNS(ns,"title"); title.textContent=`${record.experiment_id}: ${number(record.score)}`;point.append(title); }
    if (records.length < 9 || i===0 || i===records.length-1) add("text",{x:x(i),y:168,"text-anchor":"middle",fill:"#73877e","font-size":9},record.experiment_id);
  });
  root.setAttribute("aria-label", `${objective.metric}: ${records.map(r => `${r.experiment_id} ${number(r.score)}`).join(", ")}. Target ${number(objective.target)}.`);
  root.replaceChildren(svg);
}
function renderEvents(events, failures) {
  $("events").replaceChildren(...events.slice(-30).reverse().map(event => {
    const item = node("li"), time = new Date(event.timestamp).toLocaleTimeString();
    item.append(node("strong", `${event.experiment_id} · ${friendly(event.state)}`), node("small", `${time}${event.job_status ? " · " + friendly(event.job_status) : ""}`)); return item;
  }));
  $("failures").replaceChildren(...failures.map(f => node("p", `Preparation ${f.category}: ${f.message}`, "failure")));
}
async function showDetail(experimentId) {
  const version = ++detailVersion, runId = selectedRun;
  const record = await api(`/api/runs/${encodeURIComponent(runId)}/experiments/${encodeURIComponent(experimentId)}`);
  if (version !== detailVersion || runId !== selectedRun || experimentId !== selectedExperiment) return;
  $("detail-title").textContent = `${record.experiment_id} · evidence`;
  badge($("detail-decision"), record.decision || record.state);
  const content = $("detail-content"); content.replaceChildren();
  const section = (title, text) => { content.append(node("h3", title), node("p", text || "Not recorded yet.", "muted")); };
  section("Hypothesis", record.hypothesis);
  section("Intervention", record.planned_intervention);
  const plan = record.candidate?.plan;
  if (plan) { section("Observation", plan.observation); section("Diagnosis", plan.diagnosis); section("Expected effect", plan.expected_effect); }
  const info = node("dl");
  for (const [label,value] of [["Parent", record.parent_experiment || "Human baseline"], ["Exact commit", record.git_commit], ["Simulated job",record.job_id || "No job submitted"], ["Outputs",record.outputs_path || "Not collected yet"]]) info.append(node("dt",label),node("dd",value));
  content.append(info);
  if (record.metrics) { content.append(node("h3", "Recorded metrics")); const metrics = node("div",undefined,"metrics"); for(const [key,value] of Object.entries(record.metrics)) { const item=node("div",undefined,"metric-value"); item.append(node("span",key),node("strong",number(value)));metrics.append(item); } content.append(metrics); }
  if (record.evaluation) { section("Evaluator conclusion",record.evaluation.conclusion); for(const check of record.evaluation.constraint_results) section(`Constraint · ${check.satisfied ? "passed" : "failed"}`,`${check.metric}: ${number(check.actual)} (${check.operator} ${number(check.threshold)})`); }
  if (record.failure_type) section(`Failure · ${record.failure_type}`,record.failure_message);
  if (record.candidate?.validation) { content.append(node("h3","Preflight validation"),node("pre",JSON.stringify(record.candidate.validation,null,2))); }
  content.append(node("h3","Code diff"),node("pre",record.diff || "Baseline: no candidate diff."));
}
async function action(kind) {
  if (!selectedRun || busy) return;
  busy = true; controls(); error();
  try { await api(`/api/runs/${encodeURIComponent(selectedRun)}/${kind}`, {}); render(await api(`/api/runs/${encodeURIComponent(selectedRun)}`)); await refreshRuns(); }
  catch (exc) { error(exc.message); }
  finally { busy = false; controls(); }
}
function openCreate() { $("create-error").textContent = ""; $("create-dialog").showModal(); }
$("new-run").addEventListener("click", openCreate);
$("first-run").addEventListener("click", openCreate);
$("close-dialog").addEventListener("click", () => $("create-dialog").close());
$("check-project").addEventListener("click", checkProject);
$("refresh-runs").addEventListener("click", () => refreshRuns(!selectedRun).catch(exc => error(exc.message)));
$("run-list").addEventListener("click", event => { const button = event.target.closest("button[data-run]"); if (button && !busy) selectRun(button.dataset.run); });
$("experiments").addEventListener("click", event => { const button = event.target.closest("button[data-experiment]"); if (!button) return; selectedExperiment = button.dataset.experiment; detailKey=""; render(snapshot); });
$("resume").addEventListener("click", () => action("resume"));
$("stop").addEventListener("click", () => action("stop"));
$("create-form").addEventListener("submit", async event => {
  event.preventDefault(); if (busy) return;
  busy = true; controls(); $("create-submit").disabled=true; $("close-dialog").disabled=true; $("create-error").textContent="";
  try {
    const maxAttempts = $("max-attempts").value;
    const run = await api("/api/runs", {max_experiments:Number($("max-experiments").value), max_attempts:maxAttempts===""?null:Number(maxAttempts), scenario:$("scenario").value});
    $("create-dialog").close();
    await selectRun(run.id);
    await api(`/api/runs/${encodeURIComponent(run.id)}/resume`, {});
    if (selectedRun === run.id) render(await api(`/api/runs/${encodeURIComponent(run.id)}`));
    await refreshRuns();
  } catch (exc) { if ($("create-dialog").open) $("create-error").textContent=exc.message; else error(exc.message); }
  finally { busy=false; $("create-submit").disabled=false; $("close-dialog").disabled=false; controls(); }
});
$("create-dialog").addEventListener("cancel", event => { if(busy) event.preventDefault(); });
window.addEventListener("pagehide", () => stream?.close());
Promise.all([checkProject(),refreshRuns(true)]).catch(exc => { error(exc.message); $("connection").textContent="Local server unavailable"; });
