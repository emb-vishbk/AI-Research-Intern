// Node-only DOM control checks. No network, browser, cloud or model calls.
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const assert = require('node:assert/strict');
const staticDir = path.join(__dirname, '../src/research_intern/api/static');
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
assert.equal(ids.length, new Set(ids).size, 'HTML IDs must be unique');
function element() { return {value: '', hidden: false, disabled: false, dataset: {}, textContent: '', listeners: {},
  classList: {add() {}, remove() {}}, addEventListener(type, callback) { this.listeners[type] = callback; },
  append() {}, replaceChildren() {}, setAttribute() {}, removeAttribute() {}}; }
const elements = new Map(ids.map(id => [id, element()]));
const calls = [];
let failNext = false, requestSequence = 0;
const snapshot = {project: {status: 'contract_valid', budget_saved: true, contract_sha256: 'a'.repeat(64)},
  connections: {azure: {}, copilot: {}}, changing: false, can_start: false, can_measure_baseline: false,
  live: {configured: true, initialized: true, stopped: false,
    usage: {credits: {spent: 85000000, held: 0, remaining: 15000000, limit: 100000000}},
    credits: {remaining: 15, minimum_additional: 15, needs_credits: true, estimated_additional: null,
      estimate_note: 'No completed usage measurements yet.', usage_unconfirmed: false}},
  run: {id: 'live-project', driver: {active: false}, experiments: [], controller: {state: 'WAITING_FOR_CREDITS'},
    research_state: {blocking_reasons: []}}};
const sandbox = {console, data: snapshot, setTimeout, clearTimeout,
  document: {getElementById(id) { assert(elements.has(id), `Missing ID: ${id}`); return elements.get(id); },
    querySelectorAll() { return []; }, createElement: element},
  window: {addEventListener() {}}, crypto: {randomUUID: () => `request-${++requestSequence}`},
  onboardingControls() {}, bindOnboarding() {},
  fetch: async (url, options) => {
    if (options.method === 'POST') {
      calls.push({url, body: JSON.parse(options.body)});
      if (failNext) { failNext = false; throw new Error('Uncertain response'); }
    }
    return {ok: true, json: async () => snapshot};
  }};
vm.createContext(sandbox);
const source = fs.readFileSync(path.join(staticDir, 'app.js'), 'utf8');
new vm.Script(fs.readFileSync(path.join(staticDir, 'onboarding.js'), 'utf8')); // Syntax check.
vm.runInContext(source.replace(/^refresh\(\)\.then\(connect\).*$/m, ''), sandbox);
vm.runInContext('render = data => {snapshot = data; renderCredits(data); controls();}; online=true; render(data);', sandbox);
assert.equal(elements.get('credit-pool').hidden, false);
assert.match(elements.get('credit-guidance').textContent, /at least 15/);
assert.equal(elements.get('credit-stop').disabled, false, 'Stop stays available when credits run out');
assert.equal(elements.get('add-credits').disabled, false);
assert.equal(elements.get('start').disabled, true);

(async () => {
  elements.get('additional-credits').value = '50';
  failNext = true;
  await elements.get('credit-form').listeners.submit({preventDefault() {}});
  await elements.get('credit-form').listeners.submit({preventDefault() {}});
  assert.equal(calls[0].body.request_id, calls[1].body.request_id, 'Retry must not double-add');
  assert.equal(calls[1].body.additional_credits, 50);
  assert(calls.every(c => c.url === '/api/project/credits'), 'Adding allowance must not start research');
  await elements.get('credit-stop').listeners.click();
  assert.equal(calls.at(-1).url, '/api/runs/live-project/stop');
  snapshot.live.stopped = true;
  snapshot.run.research_state.blocking_reasons = ['HUMAN_STOP'];
  vm.runInContext('render(data);', sandbox);
  assert.equal(elements.get('add-credits').disabled, true);
  assert.equal(elements.get('resume').hidden, true);
  assert.match(elements.get('credit-guidance').textContent, /cannot restart/);
  snapshot.run.experiments = [{state: 'RUNNING'}];
  vm.runInContext('controls();', sandbox);
  assert.equal(elements.get('stop').disabled, false, 'Cancellation can be retried after an error');
  console.log(`Credit UI checks passed; ${ids.length} unique HTML IDs.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
