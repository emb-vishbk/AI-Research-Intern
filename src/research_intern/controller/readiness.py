"""Read-only setup diagnostics. Never infer approval from files or saved scores."""

from research_intern.domain.experiments import is_finite_number


def project_readiness(project: dict) -> dict:
    checks = []

    def check(code, passed, message):
        checks.append({"code": code, "ready": bool(passed), "message": message})

    check("SOURCE_VALID", project.get("status") == "contract_valid", "Load source with a valid experiment contract.")
    check("SOURCE_PREPARED", project.get("workspace_prepared"), "Finalize policy and source, then prepare the exact Git workspace.")
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
        ("MEASURED_BASELINE", "Import a provenance-verified measured EXP-000; historical development scores are not an accepted baseline."),
        ("LIVE_AGENT", "Connect and authorize the restricted Copilot adapter; authentication is human-owned."),
        ("AZURE_BINDINGS", "Verify workspace, GPU count, image digest and versioned train/validation and weights assets."),
        ("TRUSTED_SCORING", "Verify output fingerprint/source identity and scoring independently of editable training code."),
        ("LIVE_CONTROLLER", "Connect live ledger mode, service reservations, recovery and result collection before enabling Start."),
    ):
        check(code, False, message)
    blockers = [item for item in checks if not item["ready"]]
    return {"can_start": not blockers, "checks": checks, "blockers": blockers}