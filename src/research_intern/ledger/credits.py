"""Shared loop credit pool and evidence-based, explicitly approximate guidance."""
import json
import math
import hashlib
from statistics import mean
from research_intern.ledger.services import ServiceAdmissionError

MICROCREDITS = 1_000_000
MIN_SESSION_CREDITS = 30
MIN_SESSION_UNITS = MIN_SESSION_CREDITS * MICROCREDITS


def credit_guidance(journal, remaining_experiments: int) -> dict:
    usage = journal.usage()
    with journal._connection() as connection:
        rows = connection.execute("SELECT units,evidence FROM service_settlements WHERE service='copilot_credits'").fetchall()
    samples = [units / MICROCREDITS for units, evidence in rows
               if json.loads(evidence).get("kind") == "provider_usage"]
    remaining = usage["remaining"] / MICROCREDITS
    minimum = max(0, MIN_SESSION_CREDITS - remaining)
    estimate = (max(minimum, math.ceil(mean(samples) * max(1, remaining_experiments) * 1.25 - remaining))
                if samples else None)
    return {"remaining": remaining, "minimum_session": MIN_SESSION_CREDITS,
            "minimum_additional": math.ceil(minimum * 100) / 100,
            "estimated_additional": estimate, "sample_count": len(samples),
            "needs_credits": remaining < MIN_SESSION_CREDITS,
            "estimate_note": ("Approximate allowance for remaining experiments, using observed session usage plus 25% headroom. Retries and research difficulty can change this."
                              if samples else "No completed usage measurements yet. The minimum only enables the next Copilot session; total research cost cannot be estimated yet."),
            "usage_unconfirmed": usage["unresolved"] > 0}


def reconcile_legacy_rejections(turns, credits, request_ids: list[str], terminal_log: bytes) -> int:
    """Explicit repair of the old below-minimum session-create bug, with log proof.

    Caller holds the project RunLock. No history is deleted and unknown failures
    are not refunded. The immutable, original below-minimum intent plus a recovered
    pre-proposal failure must agree with the supplied session-create diagnostics.
    """
    text = terminal_log.decode("utf-8")
    if (turns.path != credits.path or turns.service != "copilot" or credits.service != "copilot_credits"
            or not request_ids or len(set(request_ids)) != len(request_ids)
            or text.count("CopilotClient.create_session failed") < len(request_ids)
            or text.count("Minimum session limit is 30 AI credits.") < len(request_ids)):
        raise ServiceAdmissionError("Matching session-create rejection evidence is required")
    proof = json.dumps({"kind": "legacy_session_rejected", "terminal_log_sha256": hashlib.sha256(terminal_log).hexdigest(),
                        "reason": "session.create rejected a limit below 30 credits before prompt submission"}, sort_keys=True)
    restored = 0
    with credits._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for request_id in request_ids:
            row = connection.execute("""SELECT i.units,i.payload,i.remote_id,p.stage,p.failure_type,p.checkpoint_json,p.experiment_id
                FROM service_intents i JOIN preparations p ON i.request_id='attempt-' || p.sequence
                WHERE i.service='copilot_credits' AND i.request_id=?""", (request_id,)).fetchone()
            if row is None:
                raise ServiceAdmissionError("No matching legacy preparation")
            units, payload, remote_id, stage, failure, checkpoint, experiment_id = row
            payload = json.loads(payload)
            limit = payload.get("limit")
            turn = connection.execute("SELECT remote_id FROM service_intents WHERE service='copilot' AND request_id=?", (request_id,)).fetchone()
            if (payload.get("policy") or not isinstance(limit, (int, float)) or not 0 < limit < MIN_SESSION_CREDITS
                    or units != math.ceil(limit * MICROCREDITS) or remote_id is not None or turn != (None,)
                    or stage != "RECOVERED" or failure != "PROPOSER_FAILED" or experiment_id is not None
                    or "plan" in json.loads(checkpoint)):
                raise ServiceAdmissionError("This attempt is not a proven legacy session-creation rejection")
            for service in ("copilot", "copilot_credits"):
                old = connection.execute("SELECT units,evidence FROM service_settlements WHERE service=? AND request_id=?",
                                         (service, request_id)).fetchone()
                if old is not None and old != (0, proof):
                    raise ServiceAdmissionError("Prior settlement differs; preserve it for review")
                if old is None and service == "copilot_credits": restored += units
                connection.execute("INSERT OR IGNORE INTO service_settlements(service,request_id,units,evidence) VALUES (?,?,0,?)",
                                   (service, request_id, proof))
    return restored
