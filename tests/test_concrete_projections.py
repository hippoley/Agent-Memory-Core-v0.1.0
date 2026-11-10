"""Concrete PostgreSQL FTS projection and atomic task creation tests."""
import os
from datetime import datetime, timezone
from uuid import uuid4
import pytest


@pytest.mark.integration
def test_claim_atomically_creates_fts_vector_tasks_and_fts_handler_projects():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.projections import PostgresProjectionHandlers, ProjectionWorker

    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant="fts-"+uuid4().hex
        mid=db.remember(tenant_id=tenant,subject_id="room-kitchen",
            predicate="user_preference",value={"text":"希望厨房空气清新"},
            source_id="conversation-"+uuid4().hex,
            valid_from=datetime.now(timezone.utc))
        tasks=db.conn.execute(
            """SELECT channel,status FROM memory_projection_deliveries
               WHERE event_id=%s ORDER BY channel""",(mid,)).fetchall()
        assert tasks == [("fts","pending"),("graph","pending"),("vector","pending")]
        worker=ProjectionWorker(db.conn)
        handler=PostgresProjectionHandlers()
        assert worker.consume(channel="fts",handler=handler.full_text,limit=10,event_id=mid)>=1
        projected=db.conn.execute(
            "SELECT tenant_id,document::text FROM memory_claim_search WHERE memory_id=%s",
            (mid,)).fetchone()
        assert projected and projected[0]==tenant and "kitchen" in projected[1]
        state=db.conn.execute(
            """SELECT status FROM memory_projection_deliveries
               WHERE event_id=%s AND channel='fts'""",(mid,)).fetchone()[0]
        assert state=="done"
        # Vector stays pending; FTS acknowledgement cannot mark it complete.
        assert db.conn.execute(
            """SELECT status FROM memory_projection_deliveries
               WHERE event_id=%s AND channel='vector'""",(mid,)).fetchone()[0]=="pending"
    finally:
        db.close()
