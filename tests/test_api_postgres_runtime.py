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
