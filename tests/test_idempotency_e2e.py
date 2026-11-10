"""US-02: third-party Agents can safely retry canonical writes concurrently."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from uuid import uuid4
import pytest


@pytest.mark.integration
def test_concurrent_agent_write_idempotency_real_postgres():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL required")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory

    db=PostgresMemory(dsn)
    db.migrate()
    db.close()
    tenant="idempotency-"+uuid4().hex
    key="test-agent-idempotency-key-long-enough"
    request_key="retry-"+uuid4().hex
    subject="room-"+uuid4().hex
    payload={
        "subject_id":subject,"predicate":"target_co2",
        "value":{"ppm":800},"source_id":"conversation:a",
        "valid_from":"2026-11-10T00:00:00Z",
    }
    client=TestClient(create_app(api_key=key,tenant_id=tenant,
        store_factory=lambda:PostgresMemory(dsn)))
    headers={"Authorization":"Bearer "+key,"Idempotency-Key":request_key}

    def send(_):
        response=client.post("/v1/memories",json=payload,headers=headers)
        assert response.status_code==201,response.text
        return response.json()["id"]

    with ThreadPoolExecutor(max_workers=8) as workers:
        ids=list(workers.map(send,range(12)))
    assert len(set(ids))==1

    check=PostgresMemory(dsn)
    try:
        assert check.conn.execute(
            """SELECT count(*) FROM memory_claims
               WHERE tenant_id=%s AND subject_id=%s""",
            (tenant,subject)).fetchone()[0]==1
        assert check.conn.execute(
            """SELECT count(*) FROM memory_outbox
               WHERE tenant_id=%s AND aggregate_id=%s""",
            (tenant,ids[0])).fetchone()[0]==1
    finally:
        check.close()

    # A changed retry payload with the same key MUST NOT silently overwrite.
    changed=client.post("/v1/memories",headers=headers,
                        json={**payload,"value":{"ppm":1000}})
    assert changed.status_code==409,changed.text

    # Different tenants may independently use the same key without sharing data.
    other=TestClient(create_app(
        api_key="separate-tenant-test-key-with-good-length",
        tenant_id="other-"+tenant,
        store_factory=lambda:PostgresMemory(dsn)))
    independent=other.post("/v1/memories",json=payload,headers={
        "Authorization":"Bearer separate-tenant-test-key-with-good-length",
        "Idempotency-Key":request_key,
    })
    assert independent.status_code==201,independent.text
    assert independent.json()["id"]!=ids[0]
