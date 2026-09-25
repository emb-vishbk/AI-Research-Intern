# AI Research Intern — implementation handoff

Updated: 2026-09-25. This describes the main platform independently of any ML
workload. Archived development scores and earlier workload choices do not
authorize experiments or select scientific limits.

## Implemented

- Ordinary folder/ZIP onboarding, filtering of known private/cache/environment
  paths, separate retained assets, YAML/evaluator/reference/metric discovery,
  research goal and automatic internal contract/settings generation.
- Signed-in Azure subscription/resource-group/workspace/compute selection with
  clearly labelled manual fallback; job search, explicit selection, background
  waiting and artifact collection without Copilot or duplicate submissions.
- Native one-node Azure command jobs with protected output/provenance wrapping;
  frozen independent scoring; historical baseline re-scoring with explicit
  user-attested source association and no new baseline GPU reservation.

- `LiveProject` connects the existing serial loop to Copilot and Azure adapters.
- Source preparation, evaluation confirmation and immutable live settings.
- Separate Azure and Copilot browser sign-in controls, progress/cancel/retry,
  Copilot model selection and automatically generated execution settings.
- Read-only Azure resource/Copilot authentication verification, valid for up to
  24 hours; reconnecting or restarting the web server invalidates that check.
- Azure browser credentials stay in process memory. Linux/WSL Copilot browser
  credentials use a private RAM-backed session directory, removed on normal
  shutdown; users reconnect after restarting. Windows/macOS use the system
  credential manager and refuse plaintext fallback. Legacy CLI caches are unchanged.
- Measured EXP-000 through the same exact-source execution and independent scorer
  as candidates. Simulated ledgers cannot be promoted to live evidence.
- Frozen scorer/reference files, isolated imports, identity checks, finite
  objective/constraint metrics, output hashes and durable scoring receipts.
- GPU-time, coding-turn and AI-credit reservations before service operations;
  native Copilot session credit ceiling; reservations separate from actual billing.
- Durable Azure submission identity and recovery without duplicate jobs; bounded
  source tools, full Git diff validation, best-parent selection and negative results.
- Browser/CLI configure, verify, baseline, start, resume, stop, inspect and report.
- Reports include full metrics, candidate evidence, Azure job IDs, exact source,
  independent-scoring receipts and fixed protocol/scorer hashes.
- Project `.venv` with declared Azure/web/test dependencies; Windows/WSL launcher
  correction and a fast exact-source offline test runner.

## Verification

Pre-run preparation now applies reviewed choices to legacy source preparations
with placeholder budgets, preserving the previous source and receipts. Regression
coverage includes interrupted publication, dirty source and immutable active runs.
All 74 related onboarding, project setup/upload, live-loop and API regression tests
passed after this fix. No real Azure submission or Copilot coding turn was run.

Full suite: **287 tests passed** after ordinary-project onboarding (2026-09-25),
including sign-in, folder/ZIP filtering, Azure selection, existing-run polling,
baseline re-scoring, native YAML submission, cancellation, restart and serial-loop
behavior. Tests use fake cloud/model providers and the real Azure SDK loader.
After removing the manual service-settings UI and adding preparation recovery,
all 51 focused regression tests and the final dashboard interaction checks passed.
JavaScript checks exercise the actual dashboard scripts with a simulated DOM;
real browser rendering remains subject to the browser restriction below.

Run `.venv/bin/python -B tools/check_platform.py`. Logs and the exact tested source
inventory are in `.runtime/test-results/`. Tests exercise local Git, SQLite, scorer
processes and loopback HTTP/SSE with fake external services. Coverage includes live
baseline → repeated trials → keep/reject, budgets, output validation, ambiguous
submission recovery, permissions, SDK request shape and browser API gates.

The installed Edge browser refused the headless visual check because administrator
policy disables remote debugging. Browser rendering is therefore unverified; the
actual loopback server and HTTP/SSE behavior are covered by the offline suite.
The temporary browser test server has been stopped.

## External acceptance required

The current account's Copilot model check previously returned 403 (access denied).
Licence/organization access and a real browser sign-in remain to be checked by the
researcher; the application now exposes that failure with a retry option.

No real Copilot turn, Azure job or ML improvement is claimed by this implementation
pass. A researcher supplies the workload, reviewed protocol/scorer, versioned
data/environment, authenticated services and approved numeric allowances. Then run
a measured baseline and bounded multi-trial cloud demonstration and inspect its
report. Provider charges are externally authoritative. Capability/process separation
is not an OS sandbox. The [README](README.md) contains the complete setup flow.

Keep ordinary tests offline. Preserve the single serial controller and existing
module boundaries. Do not automatically select or modify an unrelated model project.
