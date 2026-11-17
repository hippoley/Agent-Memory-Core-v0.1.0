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
              "extractor_model_declared": args.extractor_model, "checks": {}}
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
        # Search must be real and grounded. The model receives this subject
        # identifier in its utterance, not a fabricated memory ID.
        updated = call("POST", "/v1/memories/process", json={
            "text": f"{subject} now settles every three months instead of monthly.",
            "source_id": "real-probe:correction",
            "context_turns": [f"{subject} settles monthly."],
            "ground_with_memory": True,
            "atomic_batch": True,
        })
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
        checks["old_id_reported_replaced"] = old_id in updated.get("replaced", [])
        current = call("GET", "/v1/memories",
                       params={"subject_id": subject, "limit": 100}).get("items", [])
        checks["current_readback"] = len(current) == 1 and current[0].get("value") == {"months": 3}
        # Explicitly query retrieval, rather than treating a DB list as retrieval.
        search = call("POST", "/v1/search",
                      json={"query": subject, "limit": 5}).get("items", [])
        checks["retrieval_contains_new"] = any(
            hit.get("subject_id") == subject and hit.get("value") == {"months": 3}
            for hit in search)
        result["checks"] = checks
        result["passed"] = all(v is True for k, v in checks.items()
                               if k != "active_quarterly_id")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--key", default=os.getenv("MEMORY_API_KEY"))
    parser.add_argument("--extractor-model", required=True)
    parser.add_argument("--ack-disposable", action="store_true")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report = run(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
