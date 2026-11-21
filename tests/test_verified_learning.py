"""Anthropic-inspired verified-learning gates over AMC's actual handoff format."""
from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest

from agent_memory_core.verified_learning import TrustedSource, review_handoff_claim


def seal(packet_value):
    """Mimic existing AMC context-pack integrity; not an issuer signature."""
    result = deepcopy(packet_value)
    pack = result["payload"]
    raw = json.dumps({k: v for k, v in pack.items() if k != "sha256"},
                     sort_keys=True, ensure_ascii=False, allow_nan=False,
                     separators=(",", ":")).encode()
    pack["sha256"] = hashlib.sha256(raw).hexdigest()
    return result


def packet(source_id="contract:v3", period="quarterly"):
    return seal({
        "status": "accepted",
        "payload": {
            "schema": "amc.context-pack.v1",
            "subjects": ["supplier-001"],
            "claims": [{
                "id": "claim-v3", "subject_id": "supplier-001",
                "predicate": "payment_period",
                "value": {"period": period}, "source_id": source_id,
            }],
            "omitted_claim_ids": [], "unresolved_effects": [],
        },
    })


def evaluate(pack=None, source=None, **kwargs):
    return review_handoff_claim(
        accepted_handoff=pack if pack is not None else packet(),
        subject_id="supplier-001", predicate="payment_period",
        proposed_value={"period": "quarterly"},
        trusted_current_source=source if source is not None else
            TrustedSource.from_verified_document("contract:v3", {
                "payment_period": {"period": "quarterly"}}),
        **kwargs,
    )


def test_verified_claim_is_reusable_for_answer_and_plan_only():
    for use in ("answer", "plan"):
        result = evaluate(requested_use=use)
        assert result.state == "VERIFIED"
        assert result.reason == "INDEPENDENT_SOURCE_MATCH"
        assert result.allowed_uses == (use,)
        assert len(result.evidence_sha256) == 64
        assert result.claim_id == "claim-v3"
    for use in ("execute", "approve", "pay", "device_control"):
        result = evaluate(requested_use=use)
        assert result.state == "VERIFIED"
        assert result.reason == "EXECUTION_REQUIRES_SEPARATE_AUTHORITY"
        assert not result.permitted


def test_an_old_correctly_preserved_memory_is_not_current_authority():
    old = packet(source_id="contract:v2", period="quarterly")
    result = evaluate(pack=old)
    assert result.state == "STALE"
    assert result.reason == "SOURCE_REVISION_CHANGED"
    assert result.requires_new_handoff
    assert not result.permitted


def test_self_repeated_error_is_not_independent_verification():
    source = TrustedSource.from_verified_document(
        "contract:v3", {"payment_period": {"period": "monthly"}})
    result = evaluate(source=source)
    assert result.state == "CONFLICT"
    assert result.reason == "SOURCE_DISAGREES_WITH_CLAIM"
    assert result.requires_new_handoff


@pytest.mark.parametrize("pack,reason", [
    (seal({"status": "offered", "payload": packet()["payload"]}), "HANDOFF_NOT_ACCEPTED"),
    (seal({**packet(), "payload": {**packet()["payload"], "omitted_claim_ids": ["missing"]}}),
     "INCOMPLETE_OR_UNRESOLVED_CONTEXT"),
    (seal({**packet(), "payload": {**packet()["payload"], "unresolved_effects": [
        {"effect_key": "remote", "status": "unknown"}]}}),
     "INCOMPLETE_OR_UNRESOLVED_CONTEXT"),
    (seal({**packet(), "payload": {**packet()["payload"], "claims":
        packet()["payload"]["claims"] * 2}}), "AMBIGUOUS_OR_MISSING_CLAIM"),
])
def test_dangerous_handoff_state_is_denied(pack, reason):
    result = evaluate(pack=pack)
    assert result.reason == reason
    assert not result.permitted


def test_handoff_payload_mutation_fails_integrity_before_learning():
    tampered = packet()
    tampered["payload"]["claims"][0]["value"] = {"period": "monthly"}
    result = evaluate(pack=tampered)
    assert result.reason == "HANDOFF_PAYLOAD_INTEGRITY_FAILURE"
    assert not result.permitted


def test_missing_source_or_mismatched_proposal_cannot_be_promoted():
    missing = review_handoff_claim(
        accepted_handoff=packet(), subject_id="supplier-001",
        predicate="payment_period", proposed_value={"period": "quarterly"},
        trusted_current_source=None)
    assert missing.state == "UNVERIFIED"
    assert not missing.permitted
    changed = review_handoff_claim(
        accepted_handoff=packet(), subject_id="supplier-001",
        predicate="payment_period", proposed_value={"period": "monthly"},
        trusted_current_source=TrustedSource.from_verified_document(
            "contract:v3", {"payment_period": {"period": "monthly"}}))
    assert changed.reason == "PROPOSAL_DIFFERS_FROM_HANDOFF"
    assert not changed.permitted


def test_tampered_source_snapshot_is_rejected():
    source = TrustedSource.from_verified_document(
        "contract:v3", {"payment_period": {"period": "quarterly"}})
    altered = TrustedSource(source_id=source.source_id,
                            facts={"payment_period": {"period": "monthly"}},
                            sha256=source.sha256)
    assert evaluate(source=altered).reason == "SOURCE_SNAPSHOT_CORRUPT"


@pytest.mark.integration
def test_real_postgres_revision_refuses_stale_handoff_then_verifies_new_one():
    """No fake context packs: use actual PostgreSQL + AMC API handoff receipts."""
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("requires real PostgreSQL")
    import httpx
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory

    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()
    tenant = "verified-learning-" + uuid4().hex
    key = "test-" + uuid4().hex + uuid4().hex
    subject = "supplier-" + uuid4().hex
    app = create_app(api_key=key, tenant_id=tenant,
                     store_factory=lambda: PostgresMemory(dsn))
    headers = {"Authorization": "Bearer " + key}
    source_v2 = TrustedSource.from_verified_document(
        "supplier-contract:v2", {"payment_period": {"period": "monthly"}})
    source_v3 = TrustedSource.from_verified_document(
        "supplier-contract:v3", {"payment_period": {"period": "quarterly"}})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://amc.local", headers=headers,
        ) as agent:
            task_response = await agent.post("/v1/tasks", json={
                "task_type": "test.verified.learning",
                "input": {"goal": "check supplier policy before approval"},
            })
            assert task_response.status_code == 201, task_response.text
            task_id = task_response.json()["task_id"]
            original = await agent.post("/v1/memories", json={
                "subject_id": subject, "predicate": "payment_period",
                "value": {"period": "monthly"}, "source_id": source_v2.source_id,
                "valid_from": datetime.now(timezone.utc).isoformat(),
            })
            assert original.status_code == 201, original.text
            base = {"task_id": task_id, "subject_ids": [subject], "token_budget": 4096}
            stale = await agent.post("/v1/handoffs", json={
                **base, "source_agent": "A", "target_agent": "B"})
            assert stale.status_code == 201, stale.text
            revision = await agent.post("/v1/memories", json={
                "subject_id": subject, "predicate": "payment_period",
                "value": {"period": "quarterly"}, "source_id": source_v3.source_id,
                "valid_from": datetime.now(timezone.utc).isoformat(),
                "replaces_id": original.json()["id"],
            })
            assert revision.status_code == 201, revision.text
            obsolete = await agent.post(
                "/v1/handoffs/" + stale.json()["handoff_id"] + "/accept",
                json={"target_agent": "B"})
            assert obsolete.status_code == 409, obsolete.text

            offered = await agent.post("/v1/handoffs", json={
                **base, "source_agent": "A", "target_agent": "B"})
            assert offered.status_code == 201, offered.text
            accepted = await agent.post(
                "/v1/handoffs/" + offered.json()["handoff_id"] + "/accept",
                json={"target_agent": "B"})
            assert accepted.status_code == 200, accepted.text
            result = review_handoff_claim(
                accepted_handoff=accepted.json(), subject_id=subject,
                predicate="payment_period",
                proposed_value={"period": "quarterly"},
                trusted_current_source=source_v3,
                requested_use="plan",
            )
            assert result.state == "VERIFIED"
            assert result.allowed_uses == ("plan",)
            refused = review_handoff_claim(
                accepted_handoff=accepted.json(), subject_id=subject,
                predicate="payment_period",
                proposed_value={"period": "quarterly"},
                trusted_current_source=source_v3,
                requested_use="approve",
            )
            assert refused.reason == "EXECUTION_REQUIRES_SEPARATE_AUTHORITY"
            # Even if the model asks for execution, no action was performed.
            assert not refused.permitted

    asyncio.run(run())
