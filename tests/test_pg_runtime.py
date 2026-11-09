"""Integration tests require the real co-installed PostgreSQL/pgvector/AGE stack.
Enable with MEMORY_TEST_DSN. Never report these tests as passing when skipped.
"""
from datetime import datetime, timezone
from uuid import uuid4
import os
import pytest

pytestmark = pytest.mark.integration

DSN = os.getenv("MEMORY_TEST_DSN")
if not DSN:
    pytest.skip("MEMORY_TEST_DSN not set; triple-store integration not exercised", allow_module_level=True)

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.age_graph import AgeProjection


def test_real_postgres_pgvector_age():
    unique = uuid4().hex
    tenant = "test-" + unique
    db = PostgresMemory(DSN)
    try:
        db.migrate()
        mid = db.remember(
            tenant_id=tenant, subject_id="room-1", predicate="preference",
            value={"text": "quiet nights"}, source_id="user-stated",
            valid_from=datetime(2026,11,1,tzinfo=timezone.utc),
        )
        rows = db.recall(tenant_id=tenant, subject_id="room-1")
        assert len(rows) == 1 and rows[0]["id"] == mid
        assert db.recall(tenant_id="not-"+tenant,subject_id="room-1")==[]
        vec=[0.0]*384
        vec[0]=1.0
        db.store_embedding(memory_id=mid,model_id="test384",vector=vec)
        assert db.vector_search(tenant_id=tenant,model_id="test384",vector=vec)[0][0]==mid

        db.add_edge(tenant_id=tenant,from_entity="room-1",predicate="HAS_WINDOW",to_entity="window-1",
                    source_memory_id=mid)
        db.add_edge(tenant_id=tenant,from_entity="window-1",predicate="BELONGS_TO",to_entity="home-1",
                    source_memory_id=mid)
        age=AgeProjection(db.conn)
        age.initialize()
        assert age.project_pending(tenant_id=tenant).projected==2
        assert age.project_pending(tenant_id=tenant).projected==0
        assert "window-1" in age.neighbors(tenant_id=tenant,from_entity="room-1")
        assert "home-1" in age.neighbors(tenant_id=tenant,from_entity="room-1",hops=2)
        assert age.neighbors(tenant_id="not-"+tenant,from_entity="room-1")==[]
    finally:
        db.close()
