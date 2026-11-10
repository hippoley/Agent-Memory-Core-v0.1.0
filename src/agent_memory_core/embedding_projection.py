"""Offline-only embedding projector for PostgreSQL memory claims.

A caller supplies a local embedding function. The canonical claim, model ID,
dimension profile and delivery receipt remain tenant-scoped. No hosted API is
used or assumed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from uuid import UUID

from .vector_validation import vector_literal


def local_sentence_transformer(model_path: str) -> Callable[[str], list[float]]:
    """Load an already-downloaded SentenceTransformer from a local directory.

    No model identifier or remote URL is accepted in order to avoid
    unintentional network access or execution of remote model code.
    """
    path = Path(model_path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Embedding model must be an existing local directory")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Install offline model support: pip install -e '.[embedding]'"
        ) from exc
    # Newer SentenceTransformer accepts local_files_only; this is an optional
    # dependency and incompatible versions should fail closed, not download.
    model = SentenceTransformer(str(path), trust_remote_code=False,
                                local_files_only=True)

    def encode(text: str) -> list[float]:
        output = model.encode([text], normalize_embeddings=True,
                              convert_to_numpy=True)
        return [float(x) for x in output[0]]

    return encode


def make_embedding_handler(*, model_id: str,
                           embed: Callable[[str], list[float]]):
    """Return an outbox handler: (conn, tenant, event_id, claim_id, payload).

    Handler effects and outbox receipt are committed atomically by
    ProjectionOutboxWorker. An embedding failure raises, so its delivery is
    retried or dead-lettered without acknowledging success.
    """
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("A stable non-empty embedding model ID is required")
    if not callable(embed):
        raise TypeError("An embedding callable is required")

    def project(conn, tenant_id: str, event_id: str,
                aggregate_id: str, payload) -> None:
        claim_id = UUID(aggregate_id)
        row = conn.execute(
            """SELECT value FROM memory_claims
               WHERE id=%s AND tenant_id=%s""",
            (claim_id, tenant_id),
        ).fetchone()
        if row is None:
            raise ValueError("Canonical memory claim missing or wrong tenant")
        content = json.dumps(row[0], ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        vector = embed(content)
        literal = vector_literal(vector)
        dim = len(vector)
        table = {384: "memory_embeddings", 512: "memory_embeddings_512"}[dim]
        conn.execute(
            """INSERT INTO memory_embedding_models(tenant_id,model_id,dimensions)
               VALUES (%s,%s,%s)
               ON CONFLICT(tenant_id,model_id) DO NOTHING""",
            (tenant_id,model_id,dim),
        )
        profile = conn.execute(
            """SELECT dimensions FROM memory_embedding_models
               WHERE tenant_id=%s AND model_id=%s FOR UPDATE""",
            (tenant_id,model_id),
        ).fetchone()
        if profile[0] != dim:
            raise ValueError("Embedding model profile dimensions mismatch")
        written = conn.execute(
            f"""INSERT INTO {table}(memory_id,model_id,embedding)
                SELECT id,%s,%s::vector FROM memory_claims
                WHERE id=%s AND tenant_id=%s
                ON CONFLICT(memory_id,model_id)
                DO UPDATE SET embedding=EXCLUDED.embedding""",
            (model_id,literal,claim_id,tenant_id),
        )
        if written.rowcount != 1:
            raise PermissionError("Canonical memory claim is not tenant owned")

    return project
