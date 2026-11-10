"""Real pgvector outbox projection integration, no model download or cloud calls."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_offline_pgvector_projector_receipt_and_tenant_isolation():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL with pgvector required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import ProjectionOutboxWorker
    from agent_memory_core.embedding_projection import make_embedding_handler

    tenant = "vector-outbox-" + uuid4().hex
    other = "other-" + tenant
    consumer = "vector-test-" + uuid4().hex
    model_id = "fixture-384-" + uuid4().hex[:8]
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        claim = db.remember(
            tenant_id=tenant, subject_id="room", predicate="preference",
            value={"noise": "quiet"}, source_id="user",
            valid_from=datetime(2026, 11, 1, tzinfo=timezone.utc))
        vector = [0.0] * 384
        vector[0] = 1.0
        invocations = []
        def local_fixture(text):
            assert "quiet" in text
            invocations.append(text)
            return vector
        worker = ProjectionOutboxWorker(db.conn)
        handler = make_embedding_handler(model_id=model_id, embed=local_fixture)
        assert db.vector_search(tenant_id=tenant, model_id=model_id, vector=vector) == []
        assert worker.consume(consumer_id=consumer,
                event_type="memory.claim.created", handler=handler, limit=100) >= 1
        assert len(invocations) == 1
        assert db.vector_search(tenant_id=tenant, model_id=model_id,
                                vector=vector)[0][0] == claim
        assert db.vector_search(tenant_id=other, model_id=model_id,
                                vector=vector) == []
        assert worker.consume(consumer_id=consumer,
                event_type="memory.claim.created", handler=handler, limit=100) == 0
        assert len(invocations) == 1
        assert db.conn.execute(
            """SELECT count(*) FROM memory_outbox_deliveries
               WHERE event_id=%s AND consumer_id=%s
                 AND delivered_at IS NOT NULL""", (claim, consumer)
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_pgvector_projector_invalid_dimensions_dead_letter_and_recovery():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL with pgvector required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.outbox import ProjectionOutboxWorker
    from agent_memory_core.embedding_projection import make_embedding_handler

    tenant = "vector-dlq-" + uuid4().hex
    consumer = "vector-fail-" + uuid4().hex
    model_id = "fixture-512-" + uuid4().hex[:8]
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        claim = db.remember(
            tenant_id=tenant, subject_id="room", predicate="preference",
            value={"text": "clean air"}, source_id="user",
            valid_from=datetime(2026, 11, 1, tzinfo=timezone.utc))
        worker = ProjectionOutboxWorker(db.conn)
        broken = make_embedding_handler(
            model_id=model_id, embed=lambda text: [1.0] * 16)
        assert worker.consume(consumer_id=consumer, event_type="memory.claim.created",
                              handler=broken, limit=100, max_attempts=1) == 0
        assert worker.status(consumer_id=consumer, tenant_id=tenant)["dead_letter"] == 1
        assert db.conn.execute(
            "SELECT count(*) FROM memory_embedding_models WHERE tenant_id=%s",
            (tenant,)).fetchone()[0] == 0
        assert worker.retry_dead_letter(tenant_id=tenant,
                                         consumer_id=consumer, event_id=claim)
        vector = [0.0] * 512
        vector[0] = 1.0
        fixed = make_embedding_handler(
            model_id=model_id, embed=lambda text: vector)
        assert worker.consume(consumer_id=consumer, event_type="memory.claim.created",
                              handler=fixed, limit=100) >= 1
        assert worker.status(consumer_id=consumer, tenant_id=tenant)["dead_letter"] == 0
        assert db.vector_search(tenant_id=tenant, model_id=model_id,
                                vector=vector)[0][0] == claim
    finally:
        db.close()


def test_local_model_loader_rejects_missing_directory(tmp_path):
    from agent_memory_core.embedding_projection import local_sentence_transformer
    with pytest.raises(ValueError, match="existing local directory"):
        local_sentence_transformer(str(tmp_path / "not-downloaded"))
