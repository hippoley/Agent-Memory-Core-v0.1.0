"""US-01: one Agent traverses HTTP -> canonical DB -> workers -> recall.

Uses a REAL PostgreSQL/AGE/pgvector stack. Deterministic local extraction and
embeddings are TEST DOUBLES, not accuracy claims about an installed local LLM.
"""
from datetime import datetime, timezone
from uuid import uuid4
import os
import pytest

pytestmark=pytest.mark.integration


def test_agent_user_story_cross_component_runtime():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL + pgvector + AGE required")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.projections import ProjectionWorker,PostgresProjectionHandlers
    from agent_memory_core.hybrid import HybridMemorySearch
    from agent_memory_core.age_graph import AgeProjection

    dsn=os.environ["MEMORY_TEST_DSN"]
    tenant="story-"+uuid4().hex
    other="other-"+tenant
    token="story-agent-test-key-strong-1234567890"
    headers={"Authorization":"Bearer "+token}
    subject="bedroom-"+uuid4().hex[:8]

    def local_test_extractor(tenant_id):
        assert tenant_id==tenant
        return lambda text: [
            {"subject_id":subject,"predicate":"night_noise",
             "value":{"text":"quiet"},"confidence":0.99}
        ]

    init=PostgresMemory(dsn)
    try:
        init.migrate()
    finally:
        init.close()

    def search_factory():
        store=PostgresMemory(dsn)
        graph=AgeProjection(store.conn)
        graph.initialize()
        return HybridMemorySearch(store,graph=graph)

    app=create_app(api_key=token,tenant_id=tenant,
        store_factory=lambda:PostgresMemory(dsn),
        search_factory=search_factory,
        memory_extractor_factory=local_test_extractor,
        capability_flags={"semantic":True,"graph":True})
    client=TestClient(app)

    # US01-01 connection and authentication.
    assert client.get("/v1/ready",headers=headers).status_code==200
    assert client.get("/v1/ready").status_code==401

    # US01-02 locally injected extraction -> canonical PostgreSQL.
    first=client.post("/v1/memories/process",headers=headers,json={
        "text":"以后晚上卧室不要太吵","source_id":"conversation:1"})
    assert first.status_code==200,first.text
    assert len(first.json()["created"])==1,first.text
    mid=first.json()["created"][0]

    # US01-03 durable read and provenance.
    recalled=client.get("/v1/memories/"+subject,headers=headers)
    assert recalled.status_code==200 and recalled.json()["items"][0]["id"]==mid
    provenance=client.get(f"/v1/memories/{mid}/sources",headers=headers)
    assert provenance.status_code==200 and provenance.json()["sources"]

    # Capture knowledge timestamp *before* correction.
    old_db=PostgresMemory(dsn)
    try:
        known_before=old_db.current_time()
        old_db.conn.commit()
    finally:
        old_db.close()

    # US01-04 correction yields new current version while preserving history.
    second=client.post("/v1/memories/process",headers=headers,json={
        "text":"更正，晚上不必那么安静","source_id":"conversation:2",
        "candidates":[{"subject_id":subject,"predicate":"night_noise",
                       "value":{"text":"normal"},"confidence":0.99}]})
    assert second.status_code==200,second.text
    assert second.json()["replaced"]==[mid]
    new_mid=second.json()["created"][0]

    store=PostgresMemory(dsn)
    try:
        past=store.recall(tenant_id=tenant,subject_id=subject,
                          known_at=known_before,valid_at=known_before)
        current=store.recall(tenant_id=tenant,subject_id=subject)
        assert len(past)==1 and past[0]["id"]==mid
        assert len(current)==1 and current[0]["id"]==new_mid
        assert store.recall(tenant_id=other,subject_id=subject)==[]
        store.conn.commit()
    finally:
        store.close()

    # US01-05 independent FTS/vector projection from the same canonical event.
    # Deterministic vectors are fixtures; they do not measure semantic quality.
    def fixture_embedder(claim):
        assert claim["subject_id"]==subject
        vector=[0.0]*384
        vector[0]=1.0 if claim["value"].get("text")=="normal" else 0.5
        return "story-fixture-384",vector

    worker_db=PostgresMemory(dsn)
    try:
        worker=ProjectionWorker(worker_db.conn)
        handlers=PostgresProjectionHandlers(embedder=fixture_embedder)
        assert worker.consume(channel="fts",handler=handlers.full_text,
                              limit=1,filter_event_id=new_mid)==1
        assert worker.consume(channel="vector",handler=handlers.vector,
                              limit=1,filter_event_id=new_mid)==1
        projection=worker_db.conn.execute(
            """SELECT channel,status FROM memory_projection_deliveries
               WHERE event_id=%s::uuid AND channel IN ('fts','vector')
               ORDER BY channel""",(new_mid,)).fetchall()
        assert projection==[("fts","done"),("vector","done")]
        indexed=worker_db.conn.execute(
            """SELECT 1 FROM memory_claim_search WHERE memory_id=%s::uuid
               AND tenant_id=%s""",(new_mid,tenant)).fetchone()
        assert indexed is not None
        worker_db.conn.commit()
    finally:
        worker_db.close()

    # US01-06 index retrieval + canonical time validation.
    search=client.post("/v1/search",headers=headers,json={
        "query":"normal","embedding":[1.0]+[0.0]*383,
        "model_id":"story-fixture-384","limit":10})
    assert search.status_code==200,search.text
    assert any(hit["memory_id"]==new_mid for hit in search.json()["items"])
    assert all(hit["memory_id"]!=mid for hit in search.json()["items"])

    # US01-07 externally visible event page and durable cursor replay.
    page=client.get("/v1/events",headers=headers,params={"limit":100})
    assert page.status_code==200
    items=page.json()["items"]
    ids={x["event_id"] for x in items}
    assert mid in ids and new_mid in ids
    after=client.get("/v1/events",headers=headers,params={"after":mid})
    assert after.status_code==200
    assert any(x["event_id"]==new_mid for x in after.json()["items"])

    # US01-08 second tenant cannot read canonical claims or first tenant event log.
    outsider=TestClient(create_app(api_key="different-test-key-with-safe-length",
        tenant_id=other,store_factory=lambda:PostgresMemory(dsn)))
    outsider_headers={"Authorization":"Bearer different-test-key-with-safe-length"}
    assert outsider.get("/v1/memories/"+subject,headers=outsider_headers).json()["items"]==[]
    assert outsider.get("/v1/events",headers=outsider_headers).json()["items"]==[]
    assert outsider.get("/v1/events",headers=outsider_headers,
                        params={"after":mid}).status_code==422
