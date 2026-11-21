"""Test-only Provider dispatch by the current fenced AMC Worker identity.

Agent B never receives the Provider's executor credential or database access.
This is a controlled security boundary probe, not production dispatch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from urllib.request import Request, urlopen

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.worker_leases import DurableTaskLeaseStore
from agent_memory_core.durable_effects import DurableTaskEffectStore


def dispatch(*, tenant_id, task_id, effect_key, worker_id, lease_epoch, provider_url):
    token = os.environ.get("PROVIDER_EFFECT_TOKEN")
    if not token:
        raise RuntimeError("Dedicated Provider executor credential required")
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        DurableTaskLeaseStore(db.conn).assert_current(
            tenant_id=tenant_id, task_id=task_id, worker_id=worker_id,
            lease_epoch=lease_epoch)
        effect = DurableTaskEffectStore(db.conn).get_effect(
            task_id=task_id, effect_key=effect_key)
        if (effect is None or effect["status"] != "executing"
                or effect["execution_worker_id"] != worker_id
                or effect["execution_lease_epoch"] != lease_epoch):
            raise RuntimeError("Stale or unclaimed effect execution")
        request = effect["request"]
        if not isinstance(request, dict) or request.get("effect_key") != effect_key:
            raise RuntimeError("Effect request mismatch")
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode()
        if hashlib.sha256(encoded).hexdigest() != effect["request_sha256"]:
            raise RuntimeError("Effect request hash mismatch")
    finally:
        db.close()
    req = Request(
        provider_url.rstrip("/") + "/effects",
        data=json.dumps(request).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "X-AMC-Tenant": tenant_id,
                 "X-AMC-Task": task_id,
                 "X-AMC-Worker": worker_id,
                 "X-AMC-Epoch": str(lease_epoch)},
        method="POST",
    )
    with urlopen(req, timeout=8) as response:
        if response.status != 200:
            raise RuntimeError("Provider action not acknowledged")
    print("FENCED_PROVIDER_DISPATCH_FINISHED")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in ("task-id", "effect-key", "worker-id", "lease-epoch", "provider-url"):
        p.add_argument("--" + name, required=True,
                       type=int if name == "lease-epoch" else str)
    a = p.parse_args()
    dispatch(tenant_id=os.environ["MEMORY_TENANT_ID"], task_id=a.task_id,
             effect_key=a.effect_key, worker_id=a.worker_id,
             lease_epoch=a.lease_epoch, provider_url=a.provider_url)
