"""Public-SDK-only two-phase persistence acceptance across a Compose restart.

Run from the repository root:
  python scripts/check_restart_persistence.py capture
  python scripts/launch_local.py down
  python scripts/launch_local.py up --profile lite
  python scripts/check_restart_persistence.py verify

The local receipt contains IDs and expected values, never API credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / ".demo" / "restart-persistence.json"
ENV = ROOT / ".env.local"


def local_settings() -> dict[str, str]:
    if not ENV.is_file():
        raise RuntimeError("Missing .env.local: run python scripts/demo_local.py first")
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("MEMORY_API_KEY", "MEMORY_TENANT_ID"):
        if not values.get(key):
            raise RuntimeError(f"Missing {key} in .env.local")
    return values


def compose_container_ids() -> dict[str, str]:
    """Read Docker container identities; an unchanged instance is not a restart."""
    result = {}
    for service in ("memory-api", "memory-db"):
        cmd = [
            "docker", "compose", "--env-file", str(ENV),
            "-f", "compose.yaml", "-f", "compose.app.yaml",
            "ps", "-q", service,
        ]
        completed = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, check=True,
            timeout=20,
        )
        container_id = completed.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise AssertionError(f"{service}: missing or invalid running container ID")
        result[service] = container_id
    return result


def assert_restarted(receipt: dict, current: dict[str, str]) -> None:
    previous = receipt.get("container_ids")
    if not isinstance(previous, dict):
        raise AssertionError("Restart receipt has no container identity baseline")
    for service in ("memory-api", "memory-db"):
        old, new = previous.get(service), current.get(service)
        if not isinstance(old, str) or not old or not isinstance(new, str) or not new:
            raise AssertionError(f"{service}: incomplete restart identity evidence")
        if old == new:
            raise AssertionError(f"{service}: container identity unchanged; actual Compose restart not proven")


def require_rows(response: object, label: str) -> list[dict]:
    if not isinstance(response, dict) or not isinstance(response.get("items"), list):
        raise AssertionError(f"{label}: invalid canonical API result")
    return response["items"]


def verify_receipt(client, receipt: dict) -> None:
    if not isinstance(receipt, dict) or not all(
        isinstance(receipt.get(k), str) and receipt[k]
        for k in ("tenant_id", "subject_id", "original_id", "revised_id")
    ):
        raise AssertionError("Incomplete restart receipt")
    subject = receipt["subject_id"]
    current = require_rows(client.recall(subject), "Current recall")
    current_ids = {row.get("id") for row in current}
    if receipt["revised_id"] not in current_ids or receipt["original_id"] in current_ids:
        raise AssertionError("Revised memory not current, or superseded memory still current")
    history = require_rows(
        client.list_memories(subject_id=subject, include_superseded=True),
        "Full history",
    )
    by_id = {row.get("id"): row for row in history}
    old, new = by_id.get(receipt["original_id"]), by_id.get(receipt["revised_id"])
    if not old or not new:
        raise AssertionError("Original or revised memory missing from history")
    if not old.get("superseded_at"):
        raise AssertionError("Old memory is not marked superseded")
    if new.get("superseded_at") or new.get("forgotten_at"):
        raise AssertionError("Revised memory unexpectedly inactive")
    if old.get("value") != {"version": 1} or new.get("value") != {"version": 2}:
        raise AssertionError("Persistent memory values differ from original receipt")
    if old.get("subject_id") != subject or new.get("subject_id") != subject:
        raise AssertionError("Canonical readback subject differs from receipt")


def capture(client, tenant_id: str) -> None:
    if RECEIPT.exists():
        raise RuntimeError(
            f"Existing {RECEIPT}: verify it before capturing again; "
            "delete only after preserving the previous evidence."
        )
    subject = "restart-acceptance-" + uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    first = client.remember(
        subject_id=subject, predicate="restart_probe",
        value={"version": 1}, source_id=subject + ":original",
        valid_from=now, idempotency_key=subject + ":v1",
    )
    first_id = first.get("id") if isinstance(first, dict) else None
    if not first_id:
        raise AssertionError("Original write did not return a canonical ID")
    revised = client.revise(
        first_id, subject_id=subject, predicate="restart_probe",
        value={"version": 2}, source_id=subject + ":revision",
        valid_from=datetime.now(timezone.utc).isoformat(),
        idempotency_key=subject + ":v2",
    )
    revised_id = revised.get("id") if isinstance(revised, dict) else None
    if not revised_id or revised_id == first_id:
        raise AssertionError("Revision did not return a distinct canonical ID")
    receipt = {
        "tenant_id": tenant_id, "subject_id": subject,
        "original_id": first_id, "revised_id": revised_id,
    }
    verify_receipt(client, receipt)
    receipt["container_ids"] = compose_container_ids()
    RECEIPT.parent.mkdir(exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print("RESTART_BASELINE_CAPTURED")
    print(f"Receipt: {RECEIPT.relative_to(ROOT)} (no credentials)")
    print("Restart services without -v, then run: python scripts/check_restart_persistence.py verify")


def verify(client, tenant_id: str) -> None:
    if not RECEIPT.is_file():
        raise RuntimeError("No restart receipt: run capture before restarting services")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("tenant_id") != tenant_id:
        raise AssertionError("Tenant mismatch: cannot verify a different local instance")
    verify_receipt(client, receipt)
    assert_restarted(receipt, compose_container_ids())
    print("RESTART_PERSISTENCE_ACCEPTED")
    print(f"Original and revised memory IDs and history survived the restart for {receipt['subject_id']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "verify"))
    args = parser.parse_args(argv)
    settings = local_settings()
    from agent_memory_core.sdk import MemoryClient
    with MemoryClient(
        os.environ.get("MEMORY_BASE_URL", "http://127.0.0.1:8765"),
        settings["MEMORY_API_KEY"],
        tenant_id=settings["MEMORY_TENANT_ID"],
    ) as client:
        if client.ready().get("status") != "ready":
            raise RuntimeError("The authenticated API is not ready")
        if args.phase == "capture":
            capture(client, settings["MEMORY_TENANT_ID"])
        else:
            verify(client, settings["MEMORY_TENANT_ID"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"RESTART_PERSISTENCE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
