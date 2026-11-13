"""Real PostgreSQL e2e: Chinese withdrawal keeps evidence and historical rows."""
import os
from uuid import uuid4
import pytest

pytestmark = pytest.mark.integration


def test_zh_withdrawal_history_and_event_provenance():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("real PostgreSQL required")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.rule_extractor import RuleFactExtractor

    tenant = "zh-revision-" + uuid4().hex
    key = "isolated-zh-revision-integration-key-strong"
    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()

    app = create_app(
        api_key=key, tenant_id=tenant,
        store_factory=lambda: PostgresMemory(dsn),
        memory_extractor_factory=lambda _: RuleFactExtractor.from_preset("home-zh"),
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + key}

    first = client.post("/v1/memories/process", headers=headers, json={
        "text": "次卧保持安静", "source_id": "zh:before"})
    assert first.status_code == 200, first.text
    assert len(first.json()["created"]) == 1
    mid = first.json()["created"][0]

    db = PostgresMemory(dsn)
    try:
        before = db.current_time()
    finally:
        db.close()

    withdrawn = client.post("/v1/memories/process", headers=headers, json={
        "text": "次卧不用静音了，正常就好", "source_id": "zh:withdrawal"})
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["replaced"] == [mid]
    assert withdrawn.json()["relations"][0]["relation"] == "RETRACTED"

    current = client.get("/v1/memories/次卧", headers=headers)
    assert current.status_code == 200, current.text
    assert not [x for x in current.json()["items"]
                if x["predicate"] == "noise_preference"]

    src = client.get(f"/v1/memories/{mid}/sources", headers=headers)
    assert src.status_code == 200, src.text
    source_ids = {s["source_id"] for s in src.json()["sources"]}
    assert {"zh:before", "zh:withdrawal"} <= source_ids

    db = PostgresMemory(dsn)
    try:
        historic = db.recall(
            tenant_id=tenant, subject_id="次卧",
            known_at=before, valid_at=before)
        assert any(x["id"] == mid for x in historic)
        events = db.conn.execute(
            """SELECT payload FROM memory_events
               WHERE tenant_id=%s AND event_type='memory.claim.forgotten'""",
            (tenant,)).fetchall()
        assert any(e[0].get("source_id") == "zh:withdrawal" for e in events)
        assert db.recall(tenant_id="another-tenant", subject_id="次卧") == []
    finally:
        db.close()
