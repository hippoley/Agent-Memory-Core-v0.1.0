"""Runnable PostgreSQL data-flow example.

Requires the combined database from docker compose. It creates real sample
entities, source, claim, edge, observation, outbox event and object bytes.
"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import os
import hashlib
import psycopg
from agent_memory_core.objects import ObjectStore
from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.age_graph import AgeProjection

DSN=os.environ.get("MEMORY_TEST_DSN","postgresql://memory:local-dev-only@127.0.0.1:55432/memory")
TENANT="demo-home"
NOW=datetime.now(timezone.utc)
SOURCE="conversation:demo-001"
CLAIM_ID=uuid4()
EVENT_ID=uuid4()
RAW=b'{"utterance":"Bedroom window is open and CO2 is 780 ppm"}'
objects=ObjectStore(Path(os.environ.get("MEMORY_OBJECTS","./.memory-objects")))
digest=objects.put(RAW)
assert hashlib.sha256(objects.get(digest)).hexdigest()==digest

store=PostgresMemory(DSN)
try:
    store.migrate()
    with store.conn.transaction():
        store.conn.execute("CREATE EXTENSION IF NOT EXISTS age")
    age=AgeProjection(store.conn)
    age.initialize()
    with store.conn.transaction():
        store.conn.execute("""
          INSERT INTO memory_sources(tenant_id,source_id,source_type,object_sha256,
                                     observed_at,trust_level)
          VALUES (%s,%s,'conversation',%s,%s,'user_asserted')
          ON CONFLICT DO NOTHING
        """,(TENANT,SOURCE,digest,NOW))
        for eid,kind,name in [
          ("home-1","place","Home"),
          ("bedroom-1","room","Bedroom"),
          ("window-1","device","Bedroom Window"),
          ("co2-1","device","Bedroom CO2 Sensor"),
          ("agent-1","agent","Home Agent")]:
            store.conn.execute("""
              INSERT INTO memory_entities(tenant_id,entity_id,entity_type,canonical_name)
              VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING
            """,(TENANT,eid,kind,name))
        store.conn.execute("""
          INSERT INTO memory_claims(id,tenant_id,subject_id,predicate,value,source_id,
                                    valid_from,object_sha256)
          VALUES (%s,%s,'window-1','reported_state',
                  '{"state":"open","confidence":0.98}'::jsonb,%s,%s,%s)
        """,(CLAIM_ID,TENANT,SOURCE,NOW,digest))
        store.conn.execute("""
          INSERT INTO memory_edges(edge_id,tenant_id,from_entity,predicate,to_entity,source_memory_id)
          VALUES (%s,%s,'bedroom-1','contains','window-1',%s),
                 (%s,%s,'bedroom-1','monitored_by','co2-1',%s),
                 (%s,%s,'home-1','contains','bedroom-1',%s)
          ON CONFLICT DO NOTHING
        """,(uuid4(),TENANT,CLAIM_ID,uuid4(),TENANT,CLAIM_ID,uuid4(),TENANT,CLAIM_ID))
        store.conn.execute("""
          INSERT INTO memory_observations(tenant_id,device_id,source_event_id,
            observed_at,measured_value,unit,property,source_id)
          VALUES (%s,'co2-1','sensor-event-001',%s,780,'ppm','co2',%s)
          ON CONFLICT DO NOTHING
        """,(TENANT,NOW,SOURCE))
        store.conn.execute("""
          INSERT INTO memory_outbox(event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
          VALUES (%s,%s,'claim',%s,'memory.claim.created',
                  '{"project":["embedding","fts","age"]}'::jsonb)
          ON CONFLICT DO NOTHING
        """,(EVENT_ID,TENANT,str(CLAIM_ID)))
    progress=age.project_pending(tenant_id=TENANT,limit=20)
    neighbors=age.neighbors(tenant_id=TENANT,from_entity="bedroom-1",hops=1)
    claim=store.recall(tenant_id=TENANT,subject_id="window-1")
    print({"claim_count":len(claim),"graph_edges_projected":progress.projected,
           "bedroom_neighbors":neighbors,"object_sha256":digest,
           "outbox_pending":store.conn.execute(
             "SELECT count(*) FROM memory_outbox WHERE tenant_id=%s AND processed_at IS NULL",
             (TENANT,)).fetchone()[0]})
finally:
    store.close()
