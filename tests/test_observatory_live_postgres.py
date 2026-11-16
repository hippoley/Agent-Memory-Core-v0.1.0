"""Real PostgreSQL contract for the Observatory Live read path.

No simulated database responses: the exact paths embedded in observatory.html
must return current and superseded claim records and tenant-scoped events.
"""
import os
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark=pytest.mark.integration


def test_observatory_live_gets_real_claims_history_and_events():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Requires isolated real PostgreSQL test database")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory

    db=PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()

    tenant="observatory-"+uuid4().hex
    other="observatory-other-"+uuid4().hex
    token="observatory-integration-api-key-32-char"
    client=TestClient(create_app(api_key=token,tenant_id=tenant,
                                 store_factory=lambda:PostgresMemory(dsn)))
    auth={"Authorization":"Bearer "+token}
    html=client.get("/dashboard/observatory.html")
    assert html.status_code==200
    assert "Memory Observatory" in html.text
    assert '/v1/memories?limit=500&include_superseded=true' in html.text
    assert '/v1/events?limit=100' in html.text
    assert client.get("/v1/memories?limit=500&include_superseded=true").status_code==401
    assert client.get("/v1/events?limit=100").status_code==401

    first=client.post("/v1/memories",headers=auth,json={
        "subject_id":"supplier-a","predicate":"settlement_period",
        "value":{"months":1},"source_id":"contract:v1",
        "valid_from":"2026-10-01T00:00:00Z"})
    assert first.status_code==201,first.text
    old_id=first.json()["id"]
    second=client.post("/v1/memories",headers=auth,json={
        "subject_id":"supplier-a","predicate":"settlement_period",
        "value":{"months":3},"source_id":"contract:v2",
        "valid_from":"2026-11-16T00:00:00Z","replaces_id":old_id})
    assert second.status_code==201,second.text
    new_id=second.json()["id"]

    mem=client.get("/v1/memories?limit=500&include_superseded=true",headers=auth)
    assert mem.status_code==200,mem.text
    assert isinstance(mem.json()["items"],list)
    records={x["id"]:x for x in mem.json()["items"]}
    assert {old_id,new_id}<=set(records)
    assert records[new_id]["value"]=={"months":3}
    assert records[old_id]["source_id"]=="contract:v1"
    assert records[old_id]["superseded_at"] is not None

    events=client.get("/v1/events?limit=100",headers=auth)
    assert events.status_code==200,events.text
    assert isinstance(events.json()["items"],list)
    assert len(events.json()["items"])>=1

    foreign=PostgresMemory(dsn)
    try:
        foreign.remember(tenant_id=other,subject_id="supplier-a",
                         predicate="settlement_period",value={"months":99},
                         source_id="other:contract",
                         valid_from=__import__("datetime").datetime(2026,11,16,tzinfo=__import__("datetime").timezone.utc))
    finally:
        foreign.close()
    again=client.get("/v1/memories?limit=500&include_superseded=true",headers=auth)
    assert all(x["value"]!={"months":99} for x in again.json()["items"])
    assert len(again.json()["items"])==2
