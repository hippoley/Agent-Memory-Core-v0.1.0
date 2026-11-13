"""Belief revision with the *real* HTTP SDK (no mock model).

Requires a running isolated local API, MEMORY_API_KEY and a disposable tenant.
The script creates two canonical claims and never deletes them automatically.
"""
from datetime import datetime, timezone, timedelta
from os import environ
from uuid import uuid4
from urllib.parse import urlsplit
from agent_memory_core.sdk import MemoryClient

base = environ.get("MEMORY_BASE_URL", "http://127.0.0.1:8765")
parts = urlsplit(base)
if parts.scheme != "http" or parts.hostname not in ("localhost", "127.0.0.1", "::1"):
    raise SystemExit("Demo only supports a loopback HTTP API.")
key = environ.get("MEMORY_API_KEY")
if not key:
    raise SystemExit("Set MEMORY_API_KEY in your local shell; never commit it.")

subject = "demo-belief-" + uuid4().hex[:12]
start = datetime.now(timezone.utc)
with MemoryClient(base, key) as memory:
    first = memory.remember(
        subject_id=subject, predicate="night_preference",
        value={"sound": "quiet"}, source_id=f"{subject}:source:1",
        valid_from=start.isoformat(), idempotency_key=f"{subject}:request:1",
    )
    print("First claim:", first["id"])
    print("Current recall:", memory.recall(subject)["items"])

    second = memory.remember(
        subject_id=subject, predicate="night_preference",
        value={"sound": "soft_music"}, source_id=f"{subject}:source:2",
        valid_from=(start + timedelta(microseconds=1)).isoformat(),
        replaces_id=first["id"], idempotency_key=f"{subject}:request:2",
    )
    print("Correction:", second["id"], "replaces", first["id"])
    print("Current recall:", memory.recall(subject)["items"])
    print("Initial claim sources:", memory.sources(first["id"]))

print("Review the two claims in the local Console; nothing was sent to a hosted model.")
