"""Integration acceptance for independent, replayable outbox projections."""
import os
from uuid import uuid4
import pytest


@pytest.mark.integration
def test_projection_fanout_isolated_retry_and_dead_letter():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("Real PostgreSQL required")
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.projections import ProjectionWorker

    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        tenant = "projection-" + uuid4().hex
        event = uuid4()
        event_type = "memory.projection.test." + uuid4().hex
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim','example',%s,'{}'::jsonb)""",
                (event,tenant,event_type))
        worker = ProjectionWorker(db.conn, max_attempts=2)
        assert worker.enqueue(event_type=event_type,channels=("fts","graph")) == 2
        assert worker.enqueue(event_type=event_type,channels=("fts","graph")) == 0
        # End the setup transaction before the worker owns this connection.
        db.conn.commit()

        def success(conn, tenant_id, event_id, aggregate, kind, payload):
            conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'projection.ok','{}'::jsonb)
                   ON CONFLICT(tenant_id,event_id) DO NOTHING""",
                (tenant_id,event_id))

        def failure(conn, tenant_id, event_id, aggregate, kind, payload):
            conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'projection.fail','{}'::jsonb)
                   ON CONFLICT(tenant_id,event_id) DO NOTHING""",
                (tenant_id,event_id+"-failed"))
            raise RuntimeError("simulated graph outage")

        assert worker.consume(channel="fts",handler=success,limit=1,filter_event_id=str(event)) == 1
        assert worker.consume(channel="graph",handler=failure,limit=1,event_id=str(event)) == 0
        db.conn.execute(
            """UPDATE memory_projection_deliveries SET available_at=clock_timestamp()
               WHERE event_id=%s AND channel='graph'""",(event,))
        db.conn.commit()
        assert worker.consume(channel="graph",handler=failure,limit=1) == 0
        rows = db.conn.execute(
            """SELECT channel,status,attempts FROM memory_projection_deliveries
               WHERE event_id=%s ORDER BY channel""",(event,)).fetchall()
        assert rows == [("fts","done",1),("graph","dead",2)]
        assert db.conn.execute(
            "SELECT count(*) FROM memory_events WHERE tenant_id=%s",(tenant,)
        ).fetchone()[0] == 1
        db.conn.commit()
        assert worker.requeue_dead(event_id=str(event),channel="graph")
        assert worker.consume(channel="graph",handler=success,limit=1,event_id=str(event)) == 1
        rows = db.conn.execute(
            """SELECT channel,status FROM memory_projection_deliveries
               WHERE event_id=%s ORDER BY channel""",(event,)).fetchall()
        assert rows == [("fts","done"),("graph","done")]
        db.conn.commit()
        assert worker.consume(channel="graph",handler=success,limit=1) == 0
    finally:
        db.close()


def test_projection_worker_validates_configuration():
    from agent_memory_core.projections import ProjectionWorker
    with pytest.raises(ValueError):
        ProjectionWorker(None,max_attempts=0)
    worker=ProjectionWorker(None)
    with pytest.raises(ValueError):
        worker.enqueue(event_type="x",channels=("fts","fts"))
    with pytest.raises(ValueError):
        worker.consume(channel="",handler=lambda *args: None)
