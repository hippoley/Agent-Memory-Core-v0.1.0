"""Run a real local API/SDK lifecycle against the running Docker stack.

Usage:
    python scripts/smoke_local.py

Reads MEMORY_API_KEY and MEMORY_TENANT_ID from .env.local. No mocks and no
direct database access: every operation goes through the public Python SDK.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise SystemExit("Missing .env.local. Start the stack first: python scripts/launch_local.py up --profile lite")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def require_items(response, label: str) -> list[dict]:
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        raise RuntimeError(f"{label}: unexpected API response: {response!r}")
    return response["items"]


def main() -> int:
    settings = load_env(ROOT / ".env.local")
    base_url = os.getenv("MEMORY_BASE_URL", "http://127.0.0.1:8765")
    api_key = os.getenv("MEMORY_API_KEY") or settings.get("MEMORY_API_KEY")
    tenant_id = os.getenv("MEMORY_TENANT_ID") or settings.get("MEMORY_TENANT_ID")
    if not api_key or not tenant_id:
        raise SystemExit(".env.local must contain MEMORY_API_KEY and MEMORY_TENANT_ID")

    # /v1/ready is intentionally authenticated: readiness must be checked
    # with the same credential that will perform the product lifecycle.
    try:
        readiness_request = urllib.request.Request(
            base_url.rstrip("/") + "/v1/ready",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with urllib.request.urlopen(readiness_request, timeout=5) as response:
            readiness = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit(
            f"Memory API is not ready at {base_url}: {exc}\n"
            "Start it with: python scripts/launch_local.py up --profile lite"
        ) from exc
    if readiness.get("status") != "ready":
        raise SystemExit(f"API readiness check did not return ready: {readiness!r}")
    print("API_READY")

    try:
        from agent_memory_core.sdk import MemoryClient
    except ImportError as exc:
        raise SystemExit(
            "SDK is not installed in this Python environment. From the repository root run:\n"
            "  python -m pip install -e '.[api]'"
        ) from exc

    subject = "local-smoke-" + uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    with MemoryClient(base_url, api_key, tenant_id=tenant_id) as client:
        capabilities = client.capabilities()
        print("CAPABILITIES_READ")

        # Exercise the actual object store and bind the returned digest to the
        # canonical claim. The SDK owns the full round-trip; no DB shortcuts.
        evidence_blob = (
            f"Agent Memory Core local smoke evidence\\n"
            f"subject={subject}\\nobserved_at={now}\\n"
        ).encode("utf-8")
        object_ref = client.upload_object(
            evidence_blob, content_type="text/plain; charset=utf-8"
        )
        object_digest = object_ref.get("sha256") if isinstance(object_ref, dict) else None
        if not object_digest:
            raise RuntimeError(f"Object upload did not return a SHA-256 digest: {object_ref!r}")
        if client.download_object(object_digest) != evidence_blob:
            raise RuntimeError("Object storage round-trip returned different bytes")
        print(f"OBJECT_STORAGE_ROUNDTRIP_VERIFIED sha256={object_digest}")

        first = client.remember(
            subject_id=subject,
            predicate="preferred_temperature",
            value={"celsius": 20},
            source_id=f"{subject}:source-initial",
            valid_from=now,
            object_sha256=object_digest,
            idempotency_key=f"{subject}:v1",
        )
        first_id = first.get("id") if isinstance(first, dict) else None
        if not first_id:
            raise RuntimeError(f"Create did not return a memory id: {first!r}")
        print(f"MEMORY_CREATED id={first_id}")

        revised = client.revise(
            first_id,
            subject_id=subject,
            predicate="preferred_temperature",
            value={"celsius": 21},
            source_id=f"{subject}:source-correction",
            valid_from=datetime.now(timezone.utc).isoformat(),
            idempotency_key=f"{subject}:v2",
        )
        revised_id = revised.get("id") if isinstance(revised, dict) else None
        if not revised_id or revised_id == first_id:
            raise RuntimeError(f"Revision did not create a new version: {revised!r}")
        print(f"MEMORY_REVISED old={first_id} new={revised_id}")

        current = require_items(client.recall(subject), "Current recall")
        if not any(item.get("id") == revised_id for item in current):
            raise RuntimeError(f"Current recall did not return revised memory: {current!r}")
        print("CURRENT_RECALL_VERIFIED")

        history = require_items(
            client.list_memories(subject_id=subject, include_superseded=True),
            "History",
        )
        history_ids = {item.get("id") for item in history}
        if not {first_id, revised_id}.issubset(history_ids):
            raise RuntimeError(f"Revision history is incomplete: {history!r}")
        print("REVISION_HISTORY_VERIFIED")

        search = client.search(query=subject, limit=10)
        if not isinstance(search, dict) or "items" not in search:
            raise RuntimeError(f"Search returned an unexpected response: {search!r}")
        print("SEARCH_COMPLETED")

        evidence = client.sources(revised_id)
        if evidence is None:
            raise RuntimeError("Evidence lookup returned no response")
        print("EVIDENCE_LOOKUP_COMPLETED")

        events = client.events(limit=20)
        if not isinstance(events, dict) or "items" not in events:
            raise RuntimeError(f"Events returned an unexpected response: {events!r}")
        print("EVENTS_READ")

        print("LOCAL_MEMORY_RUNTIME_ACCEPTED")
        print(json.dumps({
            "base_url": base_url,
            "tenant_id": tenant_id,
            "subject_id": subject,
            "created_memory_id": first_id,
            "revised_memory_id": revised_id,
            "evidence_object_sha256": object_digest,
            "capabilities": capabilities,
            "history_count": len(history),
            "search_count": len(search["items"]),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"LOCAL_MEMORY_RUNTIME_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
