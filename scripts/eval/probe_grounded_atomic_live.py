"""Opt-in REAL model + API + PostgreSQL evidence-grounded correction probe.

No model mock, no fixture candidates, no implicit deletion. The API runtime must
already be configured with a real evidence-capable LLM extractor and PostgreSQL.
Exit nonzero unless the canonical history and final recall match. Use a newly
created disposable tenant on loopback only, not a shared API/database.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def require_loopback(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Only plain HTTP loopback API, without URL credentials, is permitted")
    return base_url.rstrip("/")


def assess_history(rows: list[dict], old_id: str, subject: str) -> dict:
    """Prove the actual old claim was retired and a quarterly claim is active."""
    selected = [row for row in rows if row.get("subject_id") == subject
                and row.get("predicate") == "settlement_period"]
    original = [row for row in selected if row.get("id") == old_id]
    replacements = [row for row in selected if row.get("id") != old_id
                    and row.get("value") == {"months": 3}
                    and not row.get("superseded_at")]
    wrong_current = [row for row in selected if not row.get("superseded_at")
                     and row.get("value") != {"months": 3}]
    return {
        "original_retired": len(original) == 1 and bool(original[0].get("superseded_at")),
        "quarterly_active": len(replacements) == 1,
        "no_wrong_current": len(wrong_current) == 0,
        "active_quarterly_id": replacements[0]["id"] if len(replacements) == 1 else None,
    }


def assess_search(hits: list[dict], *, old_id: str, new_id: str | None,
                  subject: str) -> dict[str, bool]:
    """Reject stale and unrelated hits, even when they have the right value."""
    return {
        "retrieval_contains_new": bool(new_id) and any(
            hit.get("memory_id") == new_id
            and hit.get("subject_id") == subject
            and hit.get("predicate") == "settlement_period"
            and hit.get("value") == {"months": 3}
            for hit in hits),
        "retrieval_excludes_old": all(
            hit.get("memory_id") != old_id for hit in hits),
    }



def assess_model_attestation(attestation: dict, *, model: str, provider: str) -> dict[str, bool]:
    """Tie this exact API process call to the claimed provider and model."""
    calls = attestation.get("calls")
    calls = calls if isinstance(calls, list) else []
    expected_provider = "ollama" if provider == "ollama" else "openai-compatible"
    identity_ok = bool(calls) and all(
        isinstance(call, dict)
        and call.get("requested_model") == model
        and call.get("response_model") == model
        and call.get("identity_status") == "matched"
        and isinstance(call.get("request_sha256"), str)
        and len(call["request_sha256"]) == 64
        and isinstance(call.get("response_sha256"), str)
        and len(call["response_sha256"]) == 64
        for call in calls)
    runtime_ok = provider != "ollama" or (bool(calls) and all(
        bool(call.get("model_digest")) and bool(call.get("runtime_version"))
        for call in calls))
    return {
        "model_attestation_present": bool(calls),
        "model_provider_verified": attestation.get("provider") == expected_provider,
        "model_identity_verified": attestation.get("identity_verified") is True and identity_ok,
        "model_runtime_verified": runtime_ok,
    }

def assess_grounding(hits: list[dict], *, old_id: str,
                     subject: str) -> bool:
    """The exact old claim must be available to the grounding search."""
    return any(
        hit.get("memory_id") == old_id
        and hit.get("subject_id") == subject
        and hit.get("predicate") == "settlement_period"
        and hit.get("value") == {"months": 1}
        for hit in hits)



def correction_for(subject: str, language: str) -> tuple[str, str]:
    """Use exactly the same text for grounding preflight and processing."""
    if language == "zh":
        return (
            f"供应商 {subject} 的结算周期现在改为每三个月一次，不再按月结算。",
            f"供应商 {subject} 原先每个月结算一次。",
        )
    if language == "en":
        return (
            f"{subject} now settles every three months instead of monthly.",
            f"{subject} settles monthly.",
        )
    raise ValueError("language must be zh or en")


def run(args) -> dict:
    if not args.ack_disposable:
        raise ValueError("Pass --ack-disposable only for a new isolated API tenant")
    if not args.key or len(args.key) < 24:
        raise ValueError("An isolated tenant bearer key (24+ chars) is required")
    if not args.extractor_model:
        raise ValueError("--extractor-model must identify the actual configured model")
    base = require_loopback(args.base_url)
    subject = "supplier-" + uuid4().hex[:16]
    result = {"scope": "real_model_grounded_atomic_http", "subject": subject,
              "extractor_model_declared": args.extractor_model, "language": args.language, "checks": {}}
    with httpx.Client(base_url=base, timeout=120, trust_env=False,
                      headers={"Authorization": "Bearer " + args.key}) as client:
        def call(method, path, **kw):
            response = client.request(method, path, **kw)
            response.raise_for_status()
            return response.json()

        # A fresh tenant must have no canonical history, not merely zero active
        # claims. The harness never clears prior data, even when a run fails.
        existing = call("GET", "/v1/memories",
                        params={"limit": 1, "include_superseded": "true"})
        if existing.get("items"):
            raise RuntimeError("Tenant contains history; refusing to modify it")
        first = call("POST", "/v1/memories", json={
            "subject_id": subject, "predicate": "settlement_period",
            "value": {"months": 1}, "source_id": "real-probe:initial",
            "valid_from": "2026-01-01T00:00:00+00:00",
        })
        old_id = first["id"]
        result["initial_id"] = old_id
        # The API's grounding query is the correction text. Verify that
        # its lexical/vector search can see the exact claim before calling
        # a model, otherwise a failed withdrawal is a retrieval failure.
        correction, prior_turn = correction_for(subject, args.language)
        preflight = call("POST", "/v1/search",
                         json={"query": correction, "limit": 8})
        hits = preflight.get("items", [])
        result["preflight"] = {
            "old_id_retrievable": assess_grounding(hits, old_id=old_id, subject=subject),
            "channels_used": preflight.get("channels_used", []),
            "degraded_channels": preflight.get("degraded_channels", {}),
        }
        if not result["preflight"]["old_id_retrievable"]:
            result["failure_stage"] = "grounding_retrieval"
            result["passed"] = False
            return result
        updated = call("POST", "/v1/memories/process", json={
            "text": correction,
            "source_id": "real-probe:correction",
            "context_turns": [prior_turn],
            "ground_with_memory": True,
            "atomic_batch": True,
        })
        attestation = updated.get("model_call_attestation") or {}
        result["model_attestation"] = attestation
        result["process"] = {
            "atomic_batch": updated.get("atomic_batch"),
            "replaced": updated.get("replaced"),
            "rejected": updated.get("rejected"),
        }
        history = call("GET", "/v1/memories",
                       params={"subject_id": subject, "limit": 100,
                               "include_superseded": "true"}).get("items", [])
        checks = assess_history(history, old_id, subject)
        checks["atomic_response"] = updated.get("atomic_batch") is True
        checks.update(assess_model_attestation(
            attestation, model=args.extractor_model, provider=args.provider))
        checks["old_id_reported_replaced"] = old_id in updated.get("replaced", [])
        current = call("GET", "/v1/memories",
                       params={"subject_id": subject, "limit": 100}).get("items", [])
        checks["current_readback"] = len(current) == 1 and current[0].get("value") == {"months": 3}
        # Explicitly query retrieval, rather than treating a DB list as retrieval.
        search = call("POST", "/v1/search",
                      json={"query": subject, "limit": 5}).get("items", [])
        # Search hits use memory_id, not the browse endpoint's id.
        checks.update(assess_search(
            search, old_id=old_id, new_id=checks["active_quarterly_id"],
            subject=subject))
        result["checks"] = checks
        result["passed"] = all(v is True for k, v in checks.items()
                               if k != "active_quarterly_id")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--key", default=os.getenv("MEMORY_API_KEY"))
    parser.add_argument("--extractor-model", required=True)
    parser.add_argument("--provider", choices=("ollama", "openai-compatible"), required=True)
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    parser.add_argument("--ack-disposable", action="store_true")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run(args)
    except httpx.HTTPStatusError as exc:
        # Only disclose fixed local endpoint path and HTTP status, not body,
        # request query, headers, bearer token, or provider error payload.
        report = {"scope": "real_model_grounded_atomic_http",
                  "passed": False, "failure_stage": "http",
                  "failure_type": "HTTPStatusError",
                  "http_status": exc.response.status_code,
                  "endpoint_path": exc.request.url.path}
    except Exception as exc:
        # Store a negative result even if the API or model fails. Exclude
        # exception messages: HTTP traces may contain credentials or prompts.
        report = {"scope": "real_model_grounded_atomic_http",
                  "passed": False, "failure_stage": "exception",
                  "failure_type": type(exc).__name__}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
