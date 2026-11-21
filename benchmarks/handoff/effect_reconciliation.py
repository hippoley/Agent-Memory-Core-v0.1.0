"""Read-only AMC effect-intent vs externally observed Provider receipt gate.

Never mutates memory_task_effects, never claims cryptographic attestation, and
never turns a missing receipt into proof that a remote action did not occur.
An independently deployed Provider must offer its own trustworthy receipts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class EffectAssessment:
    classification: str
    matches_recorded_intent: bool
    safe_for_automatic_completion: bool
    reason: str


def assess_effect(*, recorded: dict | None, request_id: str,
                  effect_key: str, expected_payload: dict,
                  receipt: dict | None, provider_lookup_error: bool = False
                  ) -> EffectAssessment:
    """Fail-closed cross-ledger consistency, distinct from effect authorization."""
    def outcome(category, matches=False, reason=""):
        return EffectAssessment(category, matches, False, reason)

    if not request_id or not effect_key or not isinstance(expected_payload, dict):
        return outcome("INVALID_EXPECTATION", reason="Missing expected effect identity")
    if recorded is None:
        return outcome("AMC_INTENT_MISSING", reason="No tenant-scoped AMC effect intent")
    if recorded.get("effect_key") != effect_key:
        return outcome("AMC_EFFECT_KEY_CONFLICT", reason="AMC effect key differs")
    request = {"effect_key": effect_key, "request_id": request_id,
               "payload": expected_payload}
    expected_digest = hashlib.sha256(canonical(request).encode("utf-8")).hexdigest()
    if (recorded.get("request_sha256") != expected_digest
            or recorded.get("request") != request):
        return outcome("AMC_REQUEST_CONFLICT",
                       reason="AMC effect request/hash differs from expected action")
    if provider_lookup_error:
        return outcome("PROVIDER_LOOKUP_UNKNOWN", reason="Provider lookup unavailable")
    if receipt is None:
        return outcome("PROVIDER_RECEIPT_ABSENT",
                       reason="No committed receipt; remote in-flight effects remain possible")
    if (not isinstance(receipt, dict)
            or receipt.get("status") != "committed"
            or receipt.get("request_id") != request_id
            or receipt.get("effect_key") != effect_key
            or receipt.get("payload") != expected_payload):
        return outcome("PROVIDER_RECEIPT_CONFLICT",
                       reason="Provider receipt conflicts with recorded intent")
    if recorded.get("status") != "committed":
        return outcome("MATCHED_RECEIPT_AMC_UNRESOLVED", True,
                       "Provider committed, but AMC status is not committed; "
                       "fenced reconciliation is still required")
    return outcome("RECEIPTS_CONSISTENT_READ_ONLY", True,
                   "Both records report committed; read-only comparison is not "
                   "authorization, authenticated attestation, or task completion")


def load_tenant_effect(conn, *, tenant_id: str, task_id: str,
                       effect_key: str) -> dict | None:
    """Read only a tenant-owned task effect, no direct unscoped effect lookup."""
    if not all(isinstance(v, str) and v for v in (tenant_id, task_id, effect_key)):
        raise ValueError("tenant_id, task_id and effect_key required")
    row = conn.execute(
        """SELECT e.effect_key,e.request,e.request_sha256,e.status
           FROM memory_task_effects e
           JOIN memory_durable_tasks t ON t.task_id=e.task_id
           WHERE t.tenant_id=%s AND t.task_id=%s::uuid
             AND e.effect_key=%s""", (tenant_id, task_id, effect_key),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("effect_key", "request", "request_sha256", "status"), row))
