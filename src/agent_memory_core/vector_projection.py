"""Injected local-embedder adapter for independent vector projection jobs.

The core never selects or calls a hosted model. Applications inject their
local embedding function and a stable model identifier.
"""
from __future__ import annotations

import json

from .vector_validation import vector_literal


class VectorProjection:
    def __init__(self, *, embedder, model_id: str):
        if not callable(embedder) or not model_id:
            raise ValueError("A local embedder and stable model_id are required")
        self.embedder = embedder
        self.model_id = model_id

    def __call__(self, conn, tenant_id: str, event_id: str,
                 aggregate_id: str, payload) -> None:
        if isinstance(payload, str):
            payload = json.loads(payload)
        memory_id = payload.get("memory_id") if isinstance(payload, dict) else None
        if not memory_id:
            raise ValueError("Claim projection event is missing memory_id")
        row = conn.execute(
            """SELECT subject_id,predicate,value,source_id,polarity
               FROM memory_claims WHERE tenant_id=%s AND id=%s::uuid""",
            (tenant_id, memory_id),
        ).fetchone()
        if row is None:
            raise LookupError("Canonical claim for vector projection was not found")
        subject_id, predicate, value, source_id, polarity = row
        value_text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        text = " ".join((subject_id, predicate, polarity, value_text, source_id))
        vector = self.embedder(text)
        literal = vector_literal(vector)
        dimension = len(vector)
        table = "memory_embeddings" if dimension == 384 else "memory_embeddings_512"
        conn.execute(
            """INSERT INTO memory_embedding_models(tenant_id,model_id,dimensions)
               VALUES (%s,%s,%s) ON CONFLICT(tenant_id,model_id) DO NOTHING""",
            (tenant_id, self.model_id, dimension),
        )
        known = conn.execute(
            """SELECT dimensions FROM memory_embedding_models
               WHERE tenant_id=%s AND model_id=%s FOR UPDATE""",
            (tenant_id, self.model_id),
        ).fetchone()
        if known is None or known[0] != dimension:
            raise ValueError("Embedding model ID is already bound to another dimension")
        result = conn.execute(
            f"""INSERT INTO {table}(memory_id,model_id,embedding)
                SELECT id,%s,%s::vector FROM memory_claims
                WHERE id=%s::uuid AND tenant_id=%s
                ON CONFLICT(memory_id,model_id)
                DO UPDATE SET embedding=EXCLUDED.embedding""",
            (self.model_id, literal, memory_id, tenant_id),
        )
        if result.rowcount != 1:
            raise PermissionError("Memory does not belong to requested tenant")
