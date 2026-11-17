"""Independent HTTP verification after restarting the real Memory API process.

No model invocation, no mocks, no DB mutations. This program runs in a NEW
Python process with a NEW HTTP session after the original Qwen-backed API
process has terminated and a fresh server process has started against the SAME
PostgreSQL volume.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import httpx

from probe_grounded_atomic_live import require_loopback


def verify(base_url: str, key: str, primary: dict) -> dict:
    if primary.get("passed") is not True:
        raise ValueError("Primary live probe was not successful")
    subject = primary.get("subject")
    old_id = primary.get("initial_id")
    new_id = (primary.get("checks") or {}).get("active_quarterly_id")
    if not all(isinstance(x, str) and x for x in (subject, old_id, new_id)):
        raise ValueError("Primary report is missing canonical identity evidence")
    if old_id == new_id or not subject.startswith("supplier-"):
        raise ValueError("Invalid or unsafe primary scenario")
    base = require_loopback(base_url)
    if not key or len(key) < 24:
        raise ValueError("An isolated tenant bearer key (24+ chars) is required")

    with httpx.Client(base_url=base, timeout=20, trust_env=False,
                      headers={"Authorization": "Bearer " + key}) as client:
        def request(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        ready = request("GET", "/v1/ready")
        current = request("GET", "/v1/memories",
                          params={"subject_id": subject, "limit": 100}).get("items", [])
        all_claims = request("GET", "/v1/memories", params={
            "subject_id": subject, "limit": 100,
            "include_superseded": "true"}).get("items", [])
        hits = request("POST", "/v1/search",
                       json={"query": subject, "limit": 10}).get("items", [])

    old = [x for x in all_claims if x.get("id") == old_id]
    new = [x for x in all_claims if x.get("id") == new_id]
    checks = {
        "fresh_api_postgres_ready": ready.get("status") == "ready",
        "single_current_quarterly": len(current) == 1
        and current[0].get("id") == new_id
        and current[0].get("value") == {"months": 3},
        "old_history_retained": len(old) == 1
        and old[0].get("superseded_at") is not None
        and old[0].get("value") == {"months": 1},
        "new_history_retained": len(new) == 1
        and new[0].get("superseded_at") is None
        and new[0].get("value") == {"months": 3},
        "new_search_retrievable": any(
            h.get("memory_id") == new_id and h.get("subject_id") == subject
            and h.get("predicate") == "settlement_period"
            and h.get("value") == {"months": 3} for h in hits),
        "old_search_excluded": all(h.get("memory_id") != old_id for h in hits),
    }
    return {
        "scope": "independent_new_process_api_restart_postgres_readback",
        "primary_subject": subject,
        "primary_old_id": old_id,
        "primary_new_id": new_id,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--key", default=os.getenv("MEMORY_API_KEY"))
    parser.add_argument("--primary-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        primary = json.loads(args.primary_report.read_text(encoding="utf-8"))
        result = verify(args.base_url, args.key, primary)
    except httpx.HTTPStatusError as exc:
        result = {"scope": "independent_new_process_api_restart_postgres_readback",
                  "passed": False, "failure_type": "HTTPStatusError",
                  "http_status": exc.response.status_code,
                  "endpoint_path": exc.request.url.path}
    except Exception as exc:
        # Never serialize exception messages; they can contain credentials.
        result = {"scope": "independent_new_process_api_restart_postgres_readback",
                  "passed": False, "failure_type": type(exc).__name__}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
