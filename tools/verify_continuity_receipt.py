#!/usr/bin/env python3
"""Offline, stdlib-only continuity ledger conformance probe.

A PASS means *self-consistency of supplied public API snapshots only*.
It DOES NOT attest that responses are authentic, complete, fresh, signed, or
that any physical effect actually occurred.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SHA256 = re.compile(r"^[0-9a-f]{64}$")
VALID_EFFECT_STATES = {"requested", "executing", "unknown", "reconciling",
                       "committed", "failed"}
SUPPORTED_TASK_PROFILES = {"continuity.effect.v1", "continuity.authorized-effect.v1"}


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    """Refuse ambiguous duplicate JSON keys instead of silently keeping the last."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate JSON object member")
        out[key] = value
    return out


def verify(bundle: object) -> dict:
    """Return a deterministic decision with stable reason codes."""
    def result(decision: str, *reasons: str) -> dict:
        return {"schema": "amc.continuity-ledger-probe.v1",
                "decision": decision, "reasons": list(reasons),
                "scope": "untrusted_offline_ledger_consistency",
                "physical_effect_verified": False,
                "actor_authorization_verified": False,
                "snapshot_authenticity_verified": False}

    if not isinstance(bundle, dict):
        return result("FAIL", "BUNDLE_NOT_OBJECT")
    task, effects = bundle.get("task"), bundle.get("effects")
    if not isinstance(task, dict) or not isinstance(effects, dict):
        return result("FAIL", "INVALID_PUBLIC_API_PAYLOAD")
    task_id = task.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return result("FAIL", "INVALID_TASK_ID")
    if effects.get("task_id") != task_id:
        return result("FAIL", "TASK_ID_MISMATCH")
    profile = task.get("task_type")
    if not isinstance(profile, str):
        return result("FAIL", "INVALID_TASK_PROFILE")
    if profile not in SUPPORTED_TASK_PROFILES:
        return result("UNKNOWN", "UNSUPPORTED_TASK_PROFILE")
    task_input = task.get("input")
    if not isinstance(task_input, dict):
        return result("FAIL", "INVALID_TASK_INPUT")
    required = task_input.get("required_effect_keys")
    if (not isinstance(required, list) or not 1 <= len(required) <= 20
            or any(not isinstance(x, str) or not x.strip()
                   or len(x) > 255 for x in required)
            or len(set(required)) != len(required)):
        return result("FAIL", "INVALID_REQUIRED_EFFECT_MANIFEST")
    items = effects.get("items")
    if not isinstance(items, list):
        return result("FAIL", "INVALID_EFFECT_LIST")
    index = {}
    for item in items:
        if not isinstance(item, dict):
            return result("FAIL", "INVALID_EFFECT_ENTRY")
        key = item.get("effect_key")
        if not isinstance(key, str) or not key or key in index:
            return result("FAIL", "INVALID_OR_DUPLICATE_EFFECT_KEY")
        status = item.get("status")
        if not isinstance(status, str) or status not in VALID_EFFECT_STATES:
            return result("FAIL", "INVALID_EFFECT_STATUS")
        digest = item.get("request_sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            return result("FAIL", "INVALID_EFFECT_HASH")
        count = item.get("attempt_count")
        if type(count) is not int or count < 0:
            return result("FAIL", "INVALID_ATTEMPT_COUNT")
        if status == "committed" and (count == 0 or
                                      item.get("provider_reference_recorded") is not True):
            return result("FAIL", "COMMITTED_WITHOUT_PROVIDER_REFERENCE")
        # These flags are not attestations. A v1 server may only report False.
        if item.get("physical_independence_verified") is not False or (
                item.get("causal_effect_verified") is not False):
            return result("FAIL", "UNSUPPORTED_PHYSICAL_ATTESTATION")
        index[key] = item

    task_status = task.get("status")
    if not isinstance(task_status, str) or task_status not in {
            "queued", "running", "paused", "completed", "failed", "cancelled"}:
        return result("FAIL", "INVALID_TASK_STATUS")
    if task_status != "completed":
        if any(x.get("status") == "unknown" for x in items):
            return result("UNKNOWN", "UNRESOLVED_EXTERNAL_EFFECT")
        return result("UNKNOWN", "TASK_NOT_COMPLETED")
    missing = [key for key in required if key not in index]
    if missing:
        return result("FAIL", "MISSING_REQUIRED_EFFECT")
    if any(index[key]["status"] != "committed" for key in required):
        return result("FAIL", "REQUIRED_EFFECT_NOT_COMMITTED")
    if any(x["status"] != "committed" for x in items):
        return result("FAIL", "COMPLETED_WITH_UNRESOLVED_EFFECT")
    return result("PASS", "LEDGER_CONSISTENT_ONLY")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path,
                        help="JSON containing task GET response and effects GET response")
    args = parser.parse_args(argv)
    try:
        document = json.loads(
            args.bundle.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("Nonfinite JSON numbers are not permitted")))
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"decision": "FAIL", "reasons": [
            "INVALID_INPUT_FILE"], "detail": type(exc).__name__}, sort_keys=True))
        return 1
    output = verify(document)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[output["decision"]]


if __name__ == "__main__":
    sys.exit(main())
