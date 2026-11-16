"""Real PostgreSQL strict compound RETRACT/UPSERT transaction acceptance.

Requires MEMORY_TEST_DSN; checks HTTP contract AND independent canonical SQL,
including sources, immutable event log and outbox. No simulated SQL backend.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def setup():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Requires real PostgreSQL")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    tenant = "batch-" + uuid4().hex
    key = "strict-batch-integration-token-long-enough"
    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()
    app = create_app(api_key=key, tenant_id=tenant,
                     store_factory=lambda: PostgresMemory(dsn))
    return TestClient(app), {"Authorization": "Bearer " + key}, tenant, dsn


def write(client, headers, subject, predicate, value, source="prior"):
    response = client.post("/v1/memories", headers=headers, json={
        "subject_id": subject, "predicate": predicate, "value": value,
        "source_id": source, "valid_from": "2026-10-01T00:00:00Z",
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def apply(client, headers, candidates):
    return client.post("/v1/memories/process", headers=headers, json={
        "text": "compound correction", "source_id": "conversation:compound",
        "atomic_batch": True, "candidates": candidates,
    })


def snapshots(dsn, tenant):
    from agent_memory_core.postgres import PostgresMemory
    db = PostgresMemory(dsn)
    try:
        counts = {}
        for table in ("memory_claims", "memory_sources", "memory_claim_sources",
                      "memory_events", "memory_outbox", "memory_projection_tasks"):
            counts[table] = db.conn.execute(
                f"SELECT count(*) FROM {table} WHERE tenant_id=%s", (tenant,)
            ).fetchone()[0]
        return counts
    finally:
        db.close()


def active(dsn, tenant, subject):
    from agent_memory_core.postgres import PostgresMemory
    db = PostgresMemory(dsn)
    try:
        return db.conn.execute(
            "SELECT predicate,value,id::text FROM memory_claims "
            "WHERE tenant_id=%s AND subject_id=%s AND superseded_at IS NULL "
            "ORDER BY predicate",
            (tenant, subject),
        ).fetchall()
    finally:
        db.close()


def test_compound_commit_retract_and_two_upserts_with_outbox_consistency():
    client, headers, tenant, dsn = setup()
    subject = "person-" + uuid4().hex
    old_id = write(client, headers, subject, "alert_channel", {"channel": "push"})
    before = snapshots(dsn, tenant)
    response = apply(client, headers, [
        {"operation": "RETRACT", "subject_id": subject,
         "predicate": "alert_channel", "expected_memory_id": old_id,
         "expected_value": {"channel": "push"}},
        {"operation": "UPSERT", "subject_id": subject,
         "predicate": "alert_channel", "value": {"channel": "email"}},
        {"operation": "UPSERT", "subject_id": subject,
         "predicate": "display_brightness", "value": {"level": "dim"}},
    ])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["atomic_batch"] is True
    assert body["rejected"] == 0
    assert len(body["created"]) == 2
    assert old_id in body["replaced"]
    assert [x["relation"] for x in body["relations"]] == [
        "RETRACTED", "CREATED", "CREATED",
    ]
    assert [(p, v) for p, v, _ in active(dsn, tenant, subject)] == [
        ("alert_channel", {"channel": "email"}),
        ("display_brightness", {"level": "dim"}),
    ]
    after = snapshots(dsn, tenant)
    assert after["memory_claims"] == before["memory_claims"] + 2
    assert after["memory_events"] == before["memory_events"] + 3
    assert after["memory_outbox"] == before["memory_outbox"] + 3
    from agent_memory_core.postgres import PostgresMemory
    db = PostgresMemory(dsn)
    try:
        assert db.conn.execute(
            "SELECT count(*) FROM memory_events e LEFT JOIN memory_outbox o "
            "ON o.tenant_id=e.tenant_id AND o.event_id::text=e.event_id "
            "WHERE e.tenant_id=%s AND o.event_id IS NULL", (tenant,)
        ).fetchone()[0] == 0
        assert db.conn.execute(
            "SELECT count(*) FROM memory_claims WHERE tenant_id=%s "
            "AND id=%s::uuid AND superseded_at IS NOT NULL",
            (tenant, old_id),
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_rejected_late_candidate_rolls_back_retraction_sources_events_and_outbox():
    client, headers, tenant, dsn = setup()
    subject = "person-" + uuid4().hex
    old_id = write(client, headers, subject, "alert_channel", {"channel": "push"})
    before = snapshots(dsn, tenant)
    response = apply(client, headers, [
        {"operation": "RETRACT", "subject_id": subject,
         "predicate": "alert_channel", "expected_memory_id": old_id},
        {"operation": "UPSERT", "subject_id": subject,
         "predicate": "display_brightness", "value": "not-a-structured-value"},
    ])
    assert response.status_code == 409, response.text
    assert snapshots(dsn, tenant) == before
    assert active(dsn, tenant, subject) == [
        ("alert_channel", {"channel": "push"}, old_id)
    ]


def test_unmet_retraction_compare_and_withdraw_rolls_back_prior_upsert():
    client, headers, tenant, dsn = setup()
    subject = "person-" + uuid4().hex
    write(client, headers, subject, "alert_channel", {"channel": "push"})
    before = snapshots(dsn, tenant)
    response = apply(client, headers, [
        {"operation": "UPSERT", "subject_id": subject,
         "predicate": "display_brightness", "value": {"level": "dim"}},
        {"operation": "RETRACT", "subject_id": subject,
         "predicate": "alert_channel", "expected_value": {"channel": "stale"}},
    ])
    assert response.status_code == 409, response.text
    assert snapshots(dsn, tenant) == before
    assert active(dsn, tenant, subject) == [
        ("alert_channel", {"channel": "push"}, active(dsn, tenant, subject)[0][2])
    ]


def test_competing_opposite_order_batches_serialize_consistently():
    client, headers, tenant, dsn = setup()
    subject = "person-" + uuid4().hex
    barrier = Barrier(2)
    keys = ("key_a", "key_b")

    def worker(index):
        ops = [{"operation": "UPSERT", "subject_id": subject,
                "predicate": p, "value": {"version": index}}
               for p in (keys if index == 1 else reversed(keys))]
        barrier.wait(timeout=10)
        return apply(client, headers, ops)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(worker, [1, 2]))
    assert [r.status_code for r in replies] == [200, 200], [
        (r.status_code, r.text) for r in replies
    ]
    rows = active(dsn, tenant, subject)
    assert len(rows) == 2
    assert [r[0] for r in rows] == ["key_a", "key_b"]
    assert rows[0][1] == rows[1][1]
    assert rows[0][1]["version"] in (1, 2)


def test_atomic_mode_fails_closed_for_unstable_lock_identity_and_no_store():
    client, headers, tenant, dsn = setup()
    response = apply(client, headers, [
        {"operation": "UPSERT", "subject_name": "some unresolved name",
         "predicate": "display_brightness", "value": {"level": "dim"}},
    ])
    assert response.status_code == 422
    assert all(v == 0 for v in snapshots(dsn, tenant).values())

    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    fake = type("Unsupported", (), {"close": lambda self: None})
    unavailable = TestClient(create_app(
        api_key="atomic-mode-test-api-key-long-enough",
        tenant_id="not-postgres", store_factory=lambda: fake()
    ))
    denied = unavailable.post(
        "/v1/memories/process",
        headers={"Authorization": "Bearer atomic-mode-test-api-key-long-enough"},
        json={"text": "x", "source_id": "s", "atomic_batch": True,
              "candidates": [{"subject_id": "x", "predicate": "p",
                              "value": {"v": 1}}]},
    )
    assert denied.status_code == 503, denied.text


def test_embedding_failure_happens_after_commit_and_is_reported():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Requires real PostgreSQL")
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    from agent_memory_core.postgres import PostgresMemory
    tenant = "batch-embed-" + uuid4().hex
    def unavailable_embedding(candidate):
        raise RuntimeError("Embedding endpoint is down")
    client = TestClient(create_app(
        api_key="batch-embed-test-api-key-long-enough",
        tenant_id=tenant,
        store_factory=lambda: PostgresMemory(dsn),
        embedder=unavailable_embedding,
    ))
    response = client.post(
        "/v1/memories/process",
        headers={"Authorization": "Bearer batch-embed-test-api-key-long-enough"},
        json={"text": "x", "source_id": "s", "atomic_batch": True,
              "candidates": [{"subject_id": "s", "predicate": "p",
                              "value": {"v": 1}}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["embedding_failures"] == body["created"]
    assert len(active(dsn, tenant, "s")) == 1
