"""Experimental composed knowledge + fenced intent gateway.

This controlled probe does not grant the Agent B runtime a Provider token.
Only a dedicated Worker process can read the source trust registry and
register an effect intent. Signed knowledge DOES NOT authorize execution;
the existing fenced lease is independently required.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.postgres_source_authority import review_registry_handoff_claim
from agent_memory_core.sdk import MemoryClient

from benchmarks.handoff.fenced_worker import register_intent


def main():
    p = argparse.ArgumentParser()
    for arg in ("task-id", "handoff-id", "subject-id", "source-envelope"):
        p.add_argument("--"+arg, required=True)
    a = p.parse_args()
    tenant = os.environ["MEMORY_TENANT_ID"]
    # Untrusted Agent A/B cannot pass a forged accepted packet to the verifier;
    # the dedicated Worker obtains it using the authenticated AMC API.
    with MemoryClient(os.environ["MEMORY_API_URL"],
                      os.environ["MEMORY_API_KEY"], tenant_id=tenant) as api:
        accepted = api.accept_handoff(a.handoff_id, target_agent="agent-B")
    with open(a.source_envelope, encoding="utf-8") as handle:
        signed = json.load(handle)
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        decision = review_registry_handoff_claim(
            conn=db.conn, accepted_handoff=accepted,
            signed_source_envelope=signed,
            tenant_id=tenant, subject_id=a.subject_id,
            predicate="payment_period",
            proposed_value={"payment_period": "quarterly"},
            requested_use="plan",
        )
    finally:
        db.close()
    if decision.state != "VERIFIED" or decision.allowed_uses != ("plan",):
        raise RuntimeError("KNOWLEDGE_ADMISSION_REJECTED: " + decision.reason)
    # The decision only admits the intended policy; the separate Worker
    # still must claim the real lease and write fenced outbox/effect intent.
    result = register_intent(
        tenant_id=tenant, task_id=a.task_id, handoff_id=a.handoff_id,
        subject_id=a.subject_id, period="quarterly")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
