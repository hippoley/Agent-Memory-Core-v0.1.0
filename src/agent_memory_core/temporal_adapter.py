"""Optional Temporal adapter; durable workflow, PostgreSQL source of truth."""
from __future__ import annotations
from datetime import timedelta
from uuid import uuid5, NAMESPACE_URL
import os


def _database_dsn() -> str:
    dsn = os.getenv("MEMORY_DSN") or os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        raise RuntimeError("MEMORY_DSN is required by Temporal Activities")
    return dsn
try:
    from temporalio import activity, workflow
except ImportError as exc:
    raise RuntimeError("Install agent-memory-core[temporal]") from exc

@activity.defn(name="verify_memory_record")
def verify_memory_record(request: dict) -> dict:
    from .postgres import PostgresMemory
    db=PostgresMemory(_database_dsn())
    try:
        row=db.conn.execute(
            """SELECT id::text,subject_id,predicate,source_id,superseded_at
               FROM memory_claims WHERE tenant_id=%s AND id=%s::uuid""",
            (request["tenant_id"],request["memory_id"])).fetchone()
        if row is None:
            raise ValueError("Claim absent in authorized tenant")
        return {"tenant_id":request["tenant_id"],"memory_id":row[0],
                "subject_id":row[1],"predicate":row[2],
                "source_id":row[3],"active":row[4] is None}
    finally:
        db.close()

@activity.defn(name="write_memory_verification_receipt")
def write_memory_verification_receipt(verified: dict) -> dict:
    import json
    from .postgres import PostgresMemory
    receipt_id=uuid5(NAMESPACE_URL,
        "memory-verify:"+verified["tenant_id"]+":"+verified["memory_id"])
    db=PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        with db.conn.transaction():
            db.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim',%s,'memory.claim.verified',%s::jsonb)
                   ON CONFLICT(event_id) DO NOTHING""",
                (receipt_id,verified["tenant_id"],verified["memory_id"],
                 json.dumps(verified,sort_keys=True)))
        return {"receipt_id":str(receipt_id),**verified}
    finally:
        db.close()

@workflow.defn
class VerifyMemoryWorkflow:
    @workflow.run
    async def run(self, request: dict) -> dict:
        verified=await workflow.execute_activity(
            "verify_memory_record",request,start_to_close_timeout=timedelta(seconds=20))
        return await workflow.execute_activity(
            "write_memory_verification_receipt",verified,
            start_to_close_timeout=timedelta(seconds=20))
