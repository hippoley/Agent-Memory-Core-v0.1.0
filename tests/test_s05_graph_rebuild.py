"""S05: rebuild Apache AGE from PostgreSQL without tenant leakage.

Runs only against real co-installed PostgreSQL + AGE. A rebuilt projection is
derived; canonical edges are never rewritten during the rebuild.
"""
import os
from uuid import uuid4
import pytest

pytestmark=pytest.mark.integration

def test_tenant_graph_rebuild_recovers_lost_ledger_and_retired_edges():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL and Apache AGE required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.age_graph import AgeProjection
    db=PostgresMemory(dsn)
    a="s05-a-"+uuid4().hex
    b="s05-b-"+uuid4().hex
    try:
        db.migrate()
        graph=AgeProjection(db.conn)
        graph.initialize()
        db.add_edge(tenant_id=a,from_entity="room",predicate="HAS_WINDOW",
                    to_entity="window-1")
        db.add_edge(tenant_id=a,from_entity="room",predicate="HAS_WINDOW",
                    to_entity="window-2")
        retired=db.add_edge(tenant_id=a,from_entity="room",predicate="HAS_WINDOW",
                            to_entity="window-old")
        db.add_edge(tenant_id=b,from_entity="room",predicate="HAS_WINDOW",
                    to_entity="window-other")
        assert graph.project_pending(tenant_id=a).projected==3
        assert graph.project_pending(tenant_id=b).projected==1
        assert db.retire_edge(tenant_id=a,edge_id=retired)
        # Corrupt only the projection ledger: physical AGE edges remain.
        # Rebuild must remove stale AGE edges before replaying canonical state.
        with db.conn.transaction():
            db.conn.execute(
                """DELETE FROM memory_graph_projection p USING memory_edges e
                   WHERE p.edge_id=e.edge_id AND e.tenant_id=%s""",(a,))
        assert graph.rebuild_tenant(tenant_id=a)==2
        assert set(graph.neighbors(tenant_id=a,from_entity="room"))=={
            "window-1","window-2"}
        assert graph.neighbors(tenant_id=b,from_entity="room")==["window-other"]
        assert graph.rebuild_tenant(tenant_id=a)==2
        assert set(graph.neighbors(tenant_id=a,from_entity="room"))=={
            "window-1","window-2"}
        active=db.conn.execute(
            """SELECT count(*) FROM memory_graph_projection p
               JOIN memory_edges e ON p.edge_id=e.edge_id
               WHERE e.tenant_id=%s""",(a,)).fetchone()[0]
        assert active==2
        assert db.conn.execute(
            "SELECT count(*) FROM memory_edges WHERE tenant_id=%s",(a,)
        ).fetchone()[0]==3
    finally:
        db.close()


def test_graph_rebuild_rollback_restores_previous_graph_and_projection_ledger():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Real PostgreSQL and Apache AGE required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.age_graph import AgeProjection
    db=PostgresMemory(dsn)
    tenant="s05-atomic-"+uuid4().hex
    try:
        db.migrate()
        graph=AgeProjection(db.conn)
        graph.initialize()
        edge=db.add_edge(tenant_id=tenant,from_entity="room",
                         predicate="HAS_WINDOW",to_entity="window")
        assert graph.project_pending(tenant_id=tenant).projected==1
        original=graph._cypher
        def crash_after_delete(statement):
            outcome=original(statement)
            if "DELETE r RETURN r" in statement:
                raise RuntimeError("injected crash after AGE deletion")
            return outcome
        graph._cypher=crash_after_delete
        with pytest.raises(RuntimeError,match="injected crash"):
            graph.rebuild_tenant(tenant_id=tenant)
        graph._cypher=original
        assert graph.neighbors(tenant_id=tenant,from_entity="room")==["window"]
        assert db.conn.execute(
            "SELECT count(*) FROM memory_graph_projection WHERE edge_id=%s",
            (edge,)).fetchone()[0]==1
    finally:
        db.close()
