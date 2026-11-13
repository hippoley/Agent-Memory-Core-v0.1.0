"""Real PostgreSQL checks for Chinese multi-condition recall and contextual revision."""
from datetime import datetime, timezone
from uuid import uuid4
import os
import pytest

pytestmark = pytest.mark.integration


def test_chinese_contextual_retraction_and_composite_recall_real_postgres():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Requires isolated real PostgreSQL with AGE/pgvector")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.hybrid import HybridMemorySearch
    from agent_memory_core.api import create_app
    from agent_memory_core.rule_extractor import RuleFactExtractor

    tenant = "zh-context-" + uuid4().hex
    key = "local-integration-secret-key-strong-123456"
    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()
    aliases = {"温度": "temperature_preference", "灯光": "light_preference",
               "静音": "noise_preference", "安静": "noise_preference"}
    app = create_app(
        api_key=key, tenant_id=tenant,
        store_factory=lambda: PostgresMemory(dsn),
        search_factory=lambda: HybridMemorySearch(PostgresMemory(dsn),query_aliases=aliases),
        memory_extractor_factory=lambda _: RuleFactExtractor.from_preset("home-zh"))
    client = TestClient(app)
    auth = {"Authorization": "Bearer " + key}

    def process(text, source, **kwargs):
        response = client.post("/v1/memories/process", headers=auth,
                               json={"text":text,"source_id":source,**kwargs})
        assert response.status_code == 200, response.text
        return response.json()

    # The most recent claim for the same room is deliberately NOT temperature.
    a = process("主卧温度20度", "turn:1")
    assert a["created"], a
    process("主卧灯光调暗", "turn:2")
    process("客厅温度26度", "turn:3")
    result = client.post("/v1/search", headers=auth,
                         json={"query":"主卧温度","limit":5})
    assert result.status_code == 200, result.text
    items = result.json()["items"]
    assert items, result.text
    assert items[0]["subject_id"] == "主卧"
    assert items[0]["predicate"] == "temperature_preference"

    first = process("书房静音模式", "turn:4")
    assert first["created"]
    mid = first["created"][0]
    no_context = process("书房正常模式就行", "turn:5")
    assert no_context["created"] == [] and no_context["replaced"] == []
    before = PostgresMemory(dsn)
    try:
        known_before = before.current_time()
    finally:
        before.close()
    second = process("书房正常模式就行", "turn:6",
                     context_turns=["书房静音模式"])
    assert second["replaced"] == [mid]
    assert second["relations"][0]["relation"] == "RETRACTED"
    current = client.get("/v1/memories/书房", headers=auth).json()["items"]
    assert all(x["predicate"] != "noise_preference" for x in current)
    store = PostgresMemory(dsn)
    try:
        past = store.recall(tenant_id=tenant,subject_id="书房",
                            known_at=known_before,valid_at=known_before)
        assert any(x["id"] == mid for x in past)
        assert store.recall(tenant_id="other-tenant",subject_id="书房") == []
        # An intervening newer value must not be withdrawn by stale context.
        created = store.apply_candidate(
            tenant_id=tenant, subject_id="书房", predicate="noise_preference",
            value={"level": "normal"}, source_id="turn:newer", confidence=0.99)
        newer_id = created[0]
    finally:
        store.close()
    stale = process("书房正常模式就行", "turn:stale",
                    context_turns=["书房静音模式"])
    assert stale["replaced"] == []
    assert stale["relations"][0]["relation"] == "NOOP"
    active = client.get("/v1/memories/书房", headers=auth).json()["items"]
    assert any(x["id"] == newer_id for x in active)



def test_api_rejects_context_ambiguity_and_supplied_candidates():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app

    class DummyStore:
        def close(self): pass

    app = create_app(
        api_key="local-integration-secret-key-strong-123456",
        tenant_id="t",
        store_factory=DummyStore,
        memory_extractor_factory=lambda _: (lambda text: []))
    client = TestClient(app)
    h = {"Authorization":"Bearer local-integration-secret-key-strong-123456"}

    unsupported = client.post("/v1/memories/process",headers=h,json={
        "text":"书房正常模式就行","source_id":"t1",
        "context_turns":["书房静音模式"]})
    assert unsupported.status_code == 422

    unsupported = client.post("/v1/memories/process",headers=h,json={
        "text":"书房正常模式就行","source_id":"t2",
        "context_turns":["书房静音模式"],"candidates":[]})
    assert unsupported.status_code == 422

    oversized = client.post("/v1/memories/process",headers=h,json={
        "text":"test","source_id":"t3","context_turns":["x"*2001]})
    assert oversized.status_code == 422
