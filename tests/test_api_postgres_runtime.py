"""Call the external facade against the REAL combined PostgreSQL instance."""
import os
from uuid import uuid4
import pytest

@pytest.mark.integration
def test_public_api_roundtrip_to_postgres():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real database required")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    tenant="api-test-"+uuid4().hex
    key="test-api-key-with-strong-example-length"
    app=create_app(api_key=key,tenant_id=tenant,
                   store_factory=lambda:PostgresMemory(os.environ["MEMORY_TEST_DSN"]))
    # A standalone external consumer requires an initialized database.
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
    finally:
        db.close()
    client=TestClient(app)
    # Readiness is an authenticated, real SQL SELECT 1 (not a static status flag).
    token={"Authorization":"Bearer "+key}
    assert client.get("/v1/ready").status_code==401
    assert client.get("/v1/ready",headers=token).status_code==200
    assert client.get("/v1/ready",headers=token).json()["checks"]=={"postgresql":"ready"}

    token={"Authorization":"Bearer "+key}
    payload={"subject_id":"bedroom-1","predicate":"night_preference",
             "value":{"noise":"quiet"},"source_id":"utterance-1",
             "valid_from":"2026-11-01T00:00:00Z"}
    response=client.post("/v1/memories",json=payload,headers=token)
    assert response.status_code==201,response.text
    mid=response.json()["id"]
    found=client.get("/v1/memories/bedroom-1",headers=token)
    assert found.status_code==200,found.text
    assert any(x["id"]==mid and x["source_id"]=="utterance-1" for x in found.json()["items"])
    # Tenant cannot be selected through request body or query.
    assert client.post("/v1/memories",headers=token,json={**payload,"tenant_id":"another"}).status_code==422
    assert client.get("/v1/memories/bedroom-1").status_code==401
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        assert db.recall(tenant_id="another",subject_id="bedroom-1")==[]
    finally:
        db.close()


def test_ready_fails_closed_if_database_cannot_be_opened():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    def fail_db():
        raise ConnectionError("database unavailable")
    app=create_app(
        api_key="test-api-key-with-strong-example-length",
        tenant_id="readiness-test",store_factory=fail_db)
    response=TestClient(app).get(
        "/v1/ready",headers={"Authorization":"Bearer test-api-key-with-strong-example-length"})
    assert response.status_code==503
    assert response.json()["detail"]=="Canonical memory store is unavailable"


@pytest.mark.integration
def test_public_api_full_memory_lifecycle_on_real_postgres(tmp_path):
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real database required")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.objects import ObjectStore
    from agent_memory_core.age_graph import AgeProjection
    from agent_memory_core.hybrid import HybridMemorySearch

    tenant="api-e2e-"+uuid4().hex
    key="full-lifecycle-api-key-with-sufficient-length"
    dsn=os.environ["MEMORY_TEST_DSN"]

    def search_factory():
        db=PostgresMemory(dsn)
        graph=AgeProjection(db.conn)
        graph.initialize()
        return HybridMemorySearch(db,graph=graph)

    db=PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()

    app=create_app(
        api_key=key,tenant_id=tenant,
        store_factory=lambda:PostgresMemory(dsn),
        search_factory=search_factory,
        object_store_factory=lambda:ObjectStore(tmp_path/"objects"),
        capability_flags={"semantic":True,"graph":True,"objects":True},
    )
    client=TestClient(app)
    headers={"Authorization":"Bearer "+key}

    # Evidence object -> canonical memory -> provenance -> vector index.
    evidence=b"floorplan evidence: bedroom window"
    uploaded=client.put("/v1/objects",headers=headers,content=evidence)
    assert uploaded.status_code==201,uploaded.text
    digest=uploaded.json()["sha256"]
    claim=client.post("/v1/memories",headers=headers,json={
        "subject_id":"bedroom-1","predicate":"air_preference",
        "value":{"noise":"quiet","target_co2_ppm":800},
        "source_id":"floorplan:bedroom-1",
        "valid_from":"2026-11-09T00:00:00Z","object_sha256":digest,
    })
    assert claim.status_code==201,claim.text
    memory_id=claim.json()["id"]
    assert client.get("/v1/objects/"+digest,headers=headers).content==evidence

    vector=[0.0]*384
    vector[0]=1.0
    indexed=client.put("/v1/memories/"+memory_id+"/embedding",
        headers=headers,json={"model_id":"e2e-384","embedding":vector})
    assert indexed.status_code==200,indexed.text

    # A second tenant sharing the same physical content store cannot read the object.
    other_app=create_app(
        api_key="other-tenant-api-key-with-sufficient-length",
        tenant_id="other-"+tenant,
        store_factory=lambda:PostgresMemory(dsn),
        object_store_factory=lambda:ObjectStore(tmp_path/"objects"),
        capability_flags={"objects":True},
    )
    other=TestClient(other_app)
    denied=other.get("/v1/objects/"+digest,
        headers={"Authorization":"Bearer other-tenant-api-key-with-sufficient-length"})
    assert denied.status_code==404

    # The HTTP processing pipeline performs confidence filtering, exact dedup,
    # provenance linking and bitemporal replacement against real PostgreSQL.
    processed=client.post("/v1/memories/process",headers=headers,json={
        "text":"Quiet room, later normal noise preference.",
        "source_id":"conversation:processing-e2e",
        "confidence_threshold":0.65,
        "candidates":[
            {"subject_id":"bedroom-1","predicate":"noise_preference",
             "value":{"level":"quiet"},"confidence":0.99},
            {"subject_id":"bedroom-1","predicate":"noise_preference",
             "value":{"level":"quiet"},"confidence":0.98},
            {"subject_id":"bedroom-1","predicate":"noise_preference",
             "value":{"level":"normal"},"confidence":0.95},
            {"subject_id":"bedroom-1","predicate":"noise_preference",
             "value":{"level":"loud"},"confidence":0.1},
        ],
    })
    assert processed.status_code==200,processed.text
    assert len(processed.json()["created"])==2
    assert processed.json()["duplicates"]==1
    assert processed.json()["rejected"]==1
    assert len(processed.json()["replaced"])==1

    # Entity registry -> canonical relation -> AGE graph projection.
    for entity_id,kind,name in [
        ("bedroom-1","room","Bedroom"),
        ("window-1","device","Bedroom Window"),
    ]:
        response=client.post("/v1/entities",headers=headers,json={
            "entity_id":entity_id,"entity_type":kind,"canonical_name":name,
            "aliases":[],"attributes":{},
        })
        assert response.status_code==201,response.text
    edge=client.post("/v1/edges",headers=headers,json={
        "from_entity":"bedroom-1","predicate":"HAS_WINDOW",
        "to_entity":"window-1","source_memory_id":memory_id,
    })
    assert edge.status_code==201,edge.text
    assert edge.json()["projected_to_age"]>=1

    # Observation ingestion, idempotency, event-time query and aggregation.
    observation={"device_id":"window-1","source_event_id":"angle-001",
        "observed_at":"2026-11-09T10:00:00+08:00","property":"angle",
        "value":17.5,"unit":"degree","source_id":"window-gateway"}
    first=client.post("/v1/observations",headers=headers,json=observation)
    duplicate=client.post("/v1/observations",headers=headers,json=observation)
    assert first.status_code==201 and duplicate.status_code==200
    read=client.get("/v1/observations/window-1",headers=headers,
                    params={"property":"angle"})
    assert read.status_code==200 and read.json()["items"][0]["measured_value"]==17.5
    aggregate=client.get("/v1/observations/window-1/aggregate",headers=headers,
        params={"property":"angle","start_at":"2026-11-09T09:00:00+08:00",
                "end_at":"2026-11-09T11:00:00+08:00"})
    assert aggregate.status_code==200 and aggregate.json()["aggregate"]["count"]==1

    # Durable event replay and combined SQL/vector/graph retrieval.
    events=client.get("/v1/events",headers=headers,params={"limit":100})
    assert events.status_code==200
    assert any(e["event_type"]=="memory.observation.recorded" for e in events.json()["items"])
    search=client.post("/v1/search",headers=headers,json={
        "query":"quiet",
        "embedding":vector,"model_id":"e2e-384",
        "anchor_entity":"bedroom-1","limit":10,
    })
    assert search.status_code==200,search.text
    body=search.json()
    assert body["items"] and body["items"][0]["memory_id"]==memory_id
    assert {"sql","vector","graph"}.issubset(set(body["channels_used"]))
