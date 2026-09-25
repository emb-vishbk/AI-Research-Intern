"""Read-only setup diagnostics. Never infer approval from files or saved scores."""

from research_intern.domain.experiments import is_finite_number


def project_readiness(project: dict, live: dict | None = None) -> dict:
    live = live or {}
    checks = []

    def check(code, passed, message):
        checks.append({"code": code, "ready": bool(passed), "message": message})

    check("SOURCE_VALID", project.get("status") == "contract_valid", "Upload your project and review its detected settings.")
    check("SOURCE_PREPARED", project.get("workspace_prepared"), "Review the goal and file selections, then choose Prepare research.")
    check("EVALUATION_CONFIRMED", project.get("evaluation_status") == "confirmed", "Explicitly confirm the scientific protocol, split and resource constraints.")
    budget = project.get("budget") or {}
    check("EXPERIMENT_LIMIT", type(budget.get("max_experiments")) is int and budget["max_experiments"] > 0,
          "Approve a positive experiment allowance; zero prohibits candidate execution.")
    for key, code, message in (
        ("max_gpu_hours", "GPU_ALLOWANCE", "Approve the GPU allowance and per-job timeout."),
        ("max_ai_credits", "AI_ALLOWANCE", "Approve the AI allowance and verify provider-side spending controls."),
    ):
        value = budget.get(key)
        check(code, is_finite_number(value) and value > 0, message)
    # None of these capabilities can be established from project settings alone.
    # Do not accept browser booleans or a historical audit as evidence of readiness.
    for code, message in (
        ("MEASURED_BASELINE", "Measure and independently score EXP-000 from the approved source."),
        ("LIVE_AGENT", "Sign in to GitHub Copilot and choose an available coding model."),
        ("AZURE_BINDINGS", "Check the selected Azure workspace, GPU compute and job inputs."),
        ("TRUSTED_SCORING", "Verify output fingerprint/source identity and scoring independently of editable training code."),
        ("LIVE_CONTROLLER", "Prepare research to save the reviewed configuration and limits."),
    ):
        ready = live.get("baseline_accepted", False) if code == "MEASURED_BASELINE" else live.get("configured", False)
        if code in ("LIVE_AGENT", "AZURE_BINDINGS"):
            ready = live.get("services_verified", False)
        check(code, ready, message)
    if live.get("error"):
        check("LIVE_CONFIGURATION", False, live["error"])
    if live.get("stopped"):
        check("HUMAN_STOP", False, "This research run has a persistent human stop.")
    if live.get("continuation_blockers"):
        check("CONTINUATION", False, "Research cannot start: " + ", ".join(live["continuation_blockers"]))
    blockers = [item for item in checks if not item["ready"]]
    return {"can_start": not blockers, "checks": checks, "blockers": blockers}
