"""Test-only Provider-side AMC fencing admission.

When enabled, the simulated Provider checks the actual PostgreSQL owner and
keeps the task row locked through its independent SQLite effect commit. This
closes the local-check-to-POST gap in the controlled test, but is NOT a
production cross-database atomic transaction or a general remote API contract.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os


@contextmanager
def provider_fence(event, headers):
    if os.environ.get("PROVIDER_REQUIRE_AMC_FENCE") != "1":
        yield True
        return
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.worker_leases import DurableTaskLeaseStore, LeaseConflict

    tenant = headers.get("X-AMC-Tenant")
    task_id = headers.get("X-AMC-Task")
    worker = headers.get("X-AMC-Worker")
    try:
        epoch = int(headers.get("X-AMC-Epoch", ""))
    except (ValueError, TypeError):
        yield False
        return
    if not all([tenant, task_id, worker]) or epoch <= 0:
        yield False
        return
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        with db.conn.transaction():
            try:
                # FOR UPDATE serializes ownership change with Provider commit.
                DurableTaskLeaseStore(db.conn).assert_current(
                    tenant_id=tenant, task_id=task_id,
                    worker_id=worker, lease_epoch=epoch, lock=True)
            except LeaseConflict:
                yield False
                return
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False)
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            row = db.conn.execute(
                """SELECT e.request_sha256,e.request,e.status,
                          e.execution_worker_id,e.execution_lease_epoch
                   FROM memory_task_effects e
                   JOIN memory_durable_tasks t ON t.task_id=e.task_id
                   WHERE t.tenant_id=%s AND e.task_id=%s::uuid
                     AND e.effect_key=%s FOR UPDATE""",
                (tenant,task_id,event.get("effect_key")),
            ).fetchone()
            if (not row or row[0] != digest or row[1] != event
                    or row[2] != "executing" or row[3] != worker or row[4] != epoch):
                yield False
                return
            # For source-bound intentions, the Provider must recheck the
            # live source at admission, not trust the earlier Worker verdict.
            # This is a trusted resource-server DB lookup, NOT an Agent token.
            checkpoint_row = db.conn.execute(
                """SELECT checkpoint FROM memory_task_checkpoints
                   WHERE task_id=%s::uuid ORDER BY version DESC LIMIT 1""",
                (task_id,),
            ).fetchone()
            checkpoint = checkpoint_row[0] if checkpoint_row else None
            source = checkpoint.get("source_authority") if isinstance(checkpoint, dict) else None
            if source is not None:
                if (not isinstance(source, dict)
                        or any(not isinstance(source.get(field), str)
                               or not source[field]
                               for field in ("source_id", "subject_id", "predicate"))
                        or event.get("payload", {}).get("subject") != source["subject_id"]):
                    yield False
                    return
                active = db.conn.execute(
                    """SELECT 1 FROM memory_knowledge_source_revisions revision
                       JOIN memory_tenants tenant ON tenant.tenant_id=revision.tenant_id
                       JOIN memory_evidence_producer_keys producer
                         ON producer.issuer=revision.issuer
                        AND producer.key_id=revision.key_id
                       JOIN memory_principals principal
                         ON principal.principal_id=producer.principal_id
                       WHERE revision.tenant_id=%s
                         AND revision.subject_id=%s
                         AND revision.predicate=%s
                         AND revision.source_id=%s
                         AND revision.revoked_at IS NULL
                         AND tenant.status='active'
                         AND principal.status='active'
                         AND principal.principal_type='service'
                         AND producer.revoked_at IS NULL
                         AND producer.audience='amc-knowledge-source'
                         AND producer.not_before <= clock_timestamp()
                         AND (producer.expires_at IS NULL
                              OR producer.expires_at > clock_timestamp())
                       FOR SHARE OF revision,tenant,producer,principal""",
                    (tenant, source["subject_id"], source["predicate"],
                     source["source_id"]),
                ).fetchone()
                if active is None:
                    yield False
                    return
            yield True
    finally:
        db.close()
