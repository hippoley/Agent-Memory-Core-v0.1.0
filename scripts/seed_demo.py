#!/usr/bin/env python3
"""Prepare a demo tenant: drop test residue, then write a narratable story.

Two flags, both idempotent enough to re-run before every demo:

    python scripts/seed_demo.py --clean     # soft-forget smoke/e2e claims
    python scripts/seed_demo.py --seed      # write the demo story
    python scripts/seed_demo.py --clean --seed

Reads ``MEMORY_API_KEY`` from the environment and ``--base`` (default
http://127.0.0.1:8765). Nothing here is imported by the service; it is an
operator script over the public HTTP API, so it also works against a remote
instance. Every deletion is a soft forget: the audit trail is preserved.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

NOISE_PREFIXES = ("smoke:", "e2e:", "deploy-test:", "lifecycle-", "provider-verify:")

STORY = [
    ("demo:script:01", "主卧的空调调到 22 度"),
    ("demo:script:02", "主卧有点冷，温度改成 23 度"),
    ("demo:script:03", "客厅灯光调暗一点"),
    ("demo:script:04", "客厅灯太暗了，调亮一些"),
    ("demo:script:05", "书房保持安静，我要开会"),
    ("demo:script:06", "我不喜欢厨房的油烟味"),
    ("demo:script:07", "我对花粉过敏"),
]


def request(base: str, path: str, key: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read().decode()
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def clean(base: str, key: str) -> None:
    status, body = request(base, "/v1/memories?limit=500&include_superseded=true", key)
    if status != 200:
        sys.exit(f"browse failed: {status} {body}")
    items = body.get("items", [])
    noisy = [i for i in items
             if str(i.get("source_id", "")).startswith(NOISE_PREFIXES)
             or any(ch.isdigit() for ch in str(i.get("subject_id", ""))[-4:])
             and str(i.get("subject_id", "")).startswith("bedroom-")]
    forgotten = 0
    for item in noisy:
        if item.get("superseded_at"):
            continue
        code, _ = request(base, "/v1/memories/" + item["id"], key, method="DELETE")
        if code == 200:
            forgotten += 1
    print(f"clean: forgotten {forgotten} test claims (audit retained)")


def seed(base: str, key: str) -> None:
    for source_id, text in STORY:
        status, body = request(base, "/v1/memories/process", key, method="POST",
                               payload={"text": text, "source_id": source_id})
        if status != 200:
            print(f"  ! {text}: {status} {body}")
            continue
        relations = ", ".join(f"{r['relation']}:{r['subject_id']}/{r['predicate']}"
                              for r in body.get("relations", [])) or "no facts"
        print(f"  {text}  ->  {relations}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("MEMORY_BASE", "http://127.0.0.1:8765"))
    parser.add_argument("--clean", action="store_true", help="soft-forget smoke/e2e claims")
    parser.add_argument("--seed", action="store_true", help="write the demo story")
    args = parser.parse_args()
    key = os.environ.get("MEMORY_API_KEY")
    if not key:
        sys.exit("MEMORY_API_KEY is required")
    if not (args.clean or args.seed):
        parser.error("pass --clean and/or --seed")
    if args.clean:
        clean(args.base, key)
    if args.seed:
        seed(args.base, key)


if __name__ == "__main__":
    main()
