"""Real PostgreSQL tenant-isolation attack through two distinct REST APIs.

Run only against MEMORY_TEST_DSN pointing to an isolated integration database.
Tests verify victim history is unchanged after cross-tenant ID, search, source,
and evidence-grounded withdrawal attempts.
"""
import os
from uuid import uuid4
import pytest

pytestmark = pytest.mark.integration


def test_two_tenants_cannot_read_retract_or_launder_victim_memory():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Requires isolated PostgreSQL test service")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.hybrid import HybridMemorySearch

    uid = uuid4().hex
    tenant_a, tenant_b = "p0-victim-" + uid, "p0-attacker-" + uid
    key_a, key_b = "test-only-tenant-a-" + uid, "test-only-tenant-b-" + uid
    subject = "supplier-" + uid[:16]

    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()

    class MaliciousModel:
        def __call__(self, text):
            return []
        def extract_with_evidence(self, text, context_turns, evidence):
            return [{
                "subject_id": subject, "predicate": "settlement_period",
                "operation": "RETRACT", "value": {"reason": "malicious"},
                "expected_memory_id": victim_id,
                "expected_value": {"months": 1}, "confidence": 1.0
            }]

    def make_client(tenant, key):
        return TestClient(create_app(
            api_key=key, tenant_id=tenant,
            store_factory=lambda: PostgresMemory(dsn),
            search_factory=lambda: HybridMemorySearch(PostgresMemory(dsn)),
            memory_extractor_factory=lambda _: MaliciousModel()))

    a, b = make_client(tenant_a, key_a), make_client(tenant_b, key_b)
    ha, hb = {"Authorization": "Bearer " + key_a}, {"Authorization": "Bearer " + key_b}

    created = a.post("/v1/memories", headers=ha, json={
        "subject_id": subject, "predicate": "settlement_period",
        "value": {"months": 1}, "source_id": "victim:original",
        "valid_from":"2026-01-01T00:00:00+00:00"})
    assert created.status_code == 201, created.text
    victim_id = created.json()["id"]

    own = b.post("/v1/memories", headers=hb, json={
        "subject_id": subject, "predicate": "settlement_period",
        "value": {"months": 7}, "source_id": "attacker:own",
        "valid_from":"2026-01-01T00:00:00+00:00"})
    assert own.status_code == 201, own.text
    attacker_id = own.json()["id"]

    def snapshot():
        d = PostgresMemory(dsn)
        try:
            rows = d.list_claims(tenant_id=tenant_a, subject_id=subject,
                                 include_superseded=True)
            events = d.conn.execute(
                "SELECT event_type, payload FROM memory_events WHERE tenant_id=%s ORDER BY id",
                (tenant_a,)).fetchall()
            return rows, [(r[0], r[1]) for r in events]
        finally:
            d.close()

    before = snapshot()
    assert len(before[0]) == 1 and before[0][0]["id"] == victim_id
    # Invalid credentials cannot select a different tenant by targeting A's API.
    assert a.get("/v1/memories", headers=hb).status_code == 403
    assert a.delete("/v1/memories/" + victim_id, headers=hb).status_code == 403

    # B's *valid* credentials must never resolve A's memory by ID.
    r = b.get("/v1/memories", headers=hb,
              params={"subject_id": subject, "include_superseded": "true"})
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["items"]] == [attacker_id]
    s = b.post("/v1/search", headers=hb, json={"query": subject, "limit": 10})
    assert s.status_code == 200
    assert victim_id not in [x["memory_id"] for x in s.json()["items"]]
    assert attacker_id in [x["memory_id"] for x in s.json()["items"]]
    src = b.get("/v1/memories/" + victim_id + "/sources", headers=hb)
    assert src.status_code == 200 and src.json()["sources"] == []

    # Direct targeted delete and broad subject delete on B affect B only.
    direct = b.delete("/v1/memories/" + victim_id, headers=hb)
    assert direct.status_code == 404

    # Even a malicious model given A's ID cannot launder a withdrawal because
    # B's grounding search contains B's history, not A's canonical evidence.
    attack = b.post("/v1/memories/process", headers=hb, json={
        "text": "撤回 " + subject + " 的结算记忆",
        "source_id": "attacker:grounded",
        "context_turns": ["之前每月结算"],
        "ground_with_memory": True, "atomic_batch": True})
    assert attack.status_code == 422, attack.text

    bulk = b.delete("/v1/memories", headers=hb, params={"subject_id": subject})
    assert bulk.status_code == 200 and victim_id not in bulk.json()["forgotten"]
    assert attacker_id in bulk.json()["forgotten"]

    assert snapshot() == before, "Victim canonical history/events were modified"
    a_read = a.get("/v1/memories", headers=ha, params={"subject_id": subject})
    assert a_read.status_code == 200
    assert len(a_read.json()["items"]) == 1 and a_read.json()["items"][0]["id"] == victim_id
