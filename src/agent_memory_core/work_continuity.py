"""US-C01: bounded, immutable and tenant-scoped Agent work continuity.

Handoff acceptance only transfers context ownership. It never executes or
retries a Temporal activity or a provider effect.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from .durable_tasks import DurableTaskStore


class HandoffConflict(ValueError):
    """Snapshot changed, capacity insufficient, or handoff already accepted."""


def _wire(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(v) for v in value]
    return value


def _encoded(value):
    return json.dumps(_wire(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_encoded(value).encode("utf-8")).hexdigest()


def _tokens(value):
    # Explicit deterministic *estimate*, not a provider tokenizer.
    return (len(_encoded(value).encode("utf-8")) + 3) // 4


def _require_ids(subject_ids):
    if not isinstance(subject_ids, list) or not 1 <= len(subject_ids) <= 20:
        raise ValueError("subject_ids must contain 1..20 explicit subjects")
    if any(not isinstance(v, str) or not v or len(v) > 255 for v in subject_ids):
        raise ValueError("invalid subject_id")
    return list(dict.fromkeys(subject_ids))


class ContinuityStore:
    def __init__(self, db):
        self.db = db
        self.conn = db.conn

    def prepare_context(self, *, tenant_id, task_id, subject_ids, token_budget=2048):
        if not isinstance(token_budget, int) or isinstance(token_budget, bool) or not 256 <= token_budget <= 8192:
            raise ValueError("token_budget must be an integer from 256 to 8192")
        subjects = _require_ids(subject_ids)
        task = DurableTaskStore(self.conn).get_task(tenant_id=tenant_id, task_id=task_id)
        if task is None:
            raise LookupError("Task not found")
        effects = self.conn.execute(
            """SELECT e.effect_key,e.status FROM memory_task_effects AS e
               JOIN memory_durable_tasks AS t ON t.task_id=e.task_id
               WHERE t.tenant_id=%s AND e.task_id=%s::uuid
               ORDER BY e.effect_key""", (tenant_id, task_id)
        ).fetchall()
        pending_effects = [{"effect_key": k, "status": state} for k, state in effects
                           if state != "committed"]
        core = {
            "schema": "amc.context-pack.v1",
            "task_id": task_id,
            "task_status": task["status"],
            "workflow_id": task["workflow_id"],
            "checkpoint_version": task["checkpoint_version"],
            "checkpoint": _wire(task["checkpoint"]),
            "unresolved_effects": pending_effects,
            "subjects": subjects,
            "claims": [],
            "omitted_claim_ids": [],
            "token_estimator": "utf8_bytes_div_4_ceil",
        }
        if _tokens(core) > token_budget:
            raise HandoffConflict("Required task context exceeds token budget")
        claims = []
        for subject in subjects:
            claims.extend(self.db.list_claims(tenant_id=tenant_id, subject_id=subject,
                                              include_superseded=False, valid_at=datetime.now(timezone.utc), limit=100))
        claims = sorted(claims, key=lambda c: (c["subject_id"], c["predicate"], c["id"]))
        for claim in claims:
            item = {k: _wire(claim[k]) for k in (
                "id", "subject_id", "predicate", "value", "source_id",
                "valid_from", "valid_to", "recorded_at", "object_sha256"
            )}
            candidate = {**core, "claims": [*core["claims"], item]}
            if _tokens(candidate) <= token_budget:
                core["claims"].append(item)
            else:
                core["omitted_claim_ids"].append(item["id"])
        # Even the omission ledger is bounded; never silently drop it.
        if _tokens(core) > token_budget:
            raise HandoffConflict("Claim omission manifest exceeds token budget")
        core["estimated_tokens"] = _tokens(core)
        if _tokens(core) > token_budget:
            raise HandoffConflict("Context budget exceeded by metadata")
        core["sha256"] = _hash(core)
        return core

    def create_handoff(self, *, tenant_id, task_id, source_agent, target_agent,
                       subject_ids, token_budget=2048):
        for value in (source_agent, target_agent):
            if not isinstance(value, str) or not 1 <= len(value) <= 120:
                raise ValueError("agent labels must contain 1..120 characters")
        if source_agent == target_agent:
            raise ValueError("source_agent and target_agent must differ")
        with self.conn.transaction():
            # Lock task while snapshotting its current version and references.
            row = self.conn.execute(
                "SELECT task_id FROM memory_durable_tasks WHERE tenant_id=%s AND task_id=%s::uuid FOR SHARE",
                (tenant_id, task_id)
            ).fetchone()
            if not row:
                raise LookupError("Task not found")
            pack = self.prepare_context(tenant_id=tenant_id, task_id=task_id,
                                        subject_ids=subject_ids, token_budget=token_budget)
            ident = str(uuid4())
            self.conn.execute(
                """INSERT INTO memory_agent_handoffs
                   (handoff_id,tenant_id,task_id,source_agent,target_agent,payload,payload_sha256)
                   VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s::jsonb,%s)""",
                (ident, tenant_id, task_id, source_agent, target_agent, _encoded(pack), _hash(pack))
            )
        return {"handoff_id": ident, "status": "offered", "payload": pack,
                "payload_sha256": _hash(pack)}

    def accept_handoff(self, *, tenant_id, handoff_id, target_agent):
        if not isinstance(target_agent, str) or not target_agent:
            raise ValueError("target_agent is required")
        with self.conn.transaction():
            row = self.conn.execute(
                """SELECT task_id::text,target_agent,status,payload,payload_sha256,accepted_by
                   FROM memory_agent_handoffs
                   WHERE tenant_id=%s AND handoff_id=%s::uuid FOR UPDATE""",
                (tenant_id, handoff_id)
            ).fetchone()
            if not row:
                raise LookupError("Handoff not found")
            task_id, target, status, pack, digest, accepted_by = row
            if target != target_agent:
                raise HandoffConflict("Handoff target mismatch")
            if _hash(pack) != digest or pack.get("sha256") != _hash({
                    k: v for k, v in pack.items() if k != "sha256"}):
                raise HandoffConflict("Handoff payload integrity mismatch")
            if status == "accepted":
                if accepted_by != target_agent:
                    raise HandoffConflict("Handoff already accepted by another agent")
                return {"handoff_id": handoff_id, "status": status, "payload": pack,
                        "verified_at": None, "idempotent": True}
            task = DurableTaskStore(self.conn).get_task(tenant_id=tenant_id, task_id=task_id)
            if not task or task["checkpoint_version"] != pack["checkpoint_version"]:
                raise HandoffConflict("Task checkpoint changed since handoff")
            # Revalidate current claim *identity*, including revisions and forgets.
            for claim in pack["claims"]:
                found = self.conn.execute(
                    """SELECT id FROM memory_claims
                       WHERE tenant_id=%s AND id=%s::uuid
                         AND superseded_at IS NULL FOR SHARE""",
                    (tenant_id, claim["id"])
                ).fetchone()
                if not found:
                    raise HandoffConflict("A handoff claim is no longer current")
            # A previous omission must not be mistaken for a complete view.
            if pack["omitted_claim_ids"]:
                raise HandoffConflict("Handoff context was truncated; cannot accept as complete")
            if pack["unresolved_effects"]:
                raise HandoffConflict("External effects need reconciliation before safe continuation")
            self.conn.execute(
                """UPDATE memory_agent_handoffs
                   SET status='accepted', accepted_by=%s, accepted_at=clock_timestamp()
                   WHERE tenant_id=%s AND handoff_id=%s::uuid""",
                (target_agent, tenant_id, handoff_id)
            )
            return {"handoff_id": handoff_id, "status": "accepted", "payload": pack,
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                    "idempotent": False}
