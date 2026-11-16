"""Isolated vector-search smoke test. Nothing executes on import.

Usage (ONLY on a disposable localhost instance):
  MEMORY_API_KEY=... python scripts/eval/diag_vector.py \
    --base-url http://127.0.0.1:8767 --allow-write
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def local_url(url: str) -> str:
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or p.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Only explicitly selected loopback services may be diagnosed")
    if p.username or p.password or p.query or p.fragment:
        raise ValueError("Do not supply credentials, query, or fragment in the URL")
    return url.rstrip("/")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a small vector diagnostic into an isolated local service")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--key", default=os.getenv("MEMORY_API_KEY"))
    parser.add_argument("--allow-write", action="store_true",
                        help="I confirm the target is a disposable test instance")
    args = parser.parse_args(argv)
    if not args.allow_write:
        parser.error("This diagnostic writes memories. Pass --allow-write only for a disposable service.")
    if not args.key:
        parser.error("Provide --key or MEMORY_API_KEY")
    base = local_url(args.base_url)
    source = "diag-vector:" + uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with httpx.Client(base_url=base, timeout=60,
                      headers={"Authorization": f"Bearer {args.key}"}) as client:
        for pred, value in (
            ("completed_camping_trip", {"location": "Utah", "days": 3}),
            ("owns_pet", {"kind": "cat"}),
        ):
            response = client.post("/v1/memories", json={
                "subject_id": source, "predicate": pred, "value": value,
                "source_id": source, "valid_from": now,
            })
            response.raise_for_status()
            print("write status:", response.status_code)
        for query in ("how many days did I spend camping in the states",
                      "what animal do I keep at home"):
            response = client.post("/v1/search", json={"query": query, "limit": 5})
            response.raise_for_status()
            body = response.json()
            print("query:", query, "channels:", body.get("channels_used"),
                  "degraded:", body.get("degraded_channels"))
            for hit in body.get("items", []):
                print(hit.get("predicate"), json.dumps(hit.get("value"), ensure_ascii=False)[:80])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
