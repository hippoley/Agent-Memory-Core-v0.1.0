"""PostgreSQL full-text and CJK substring projection for canonical claims."""
from __future__ import annotations

import json


def project_claim_to_fts(conn, tenant_id: str, event_id: str,
                         aggregate_id: str, payload) -> None:
    """Idempotently materialize a canonical claim into SQL FTS/trigram indexes."""
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
        raise LookupError("Canonical claim for projection event was not found")
    subject_id, predicate, value, source_id, polarity = row
    value_text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    document = " ".join((subject_id, predicate, polarity, value_text, source_id))
    conn.execute(
        """INSERT INTO memory_fts_documents
           (tenant_id,memory_id,document_text,search_vector,updated_at)
           VALUES (%s,%s::uuid,%s,to_tsvector('simple',%s),clock_timestamp())
           ON CONFLICT(tenant_id,memory_id) DO UPDATE SET
             document_text=EXCLUDED.document_text,
             search_vector=EXCLUDED.search_vector,
             updated_at=clock_timestamp()""",
        (tenant_id, memory_id, document, document),
    )


def search_fts(conn, *, tenant_id: str, query: str, limit: int = 10) -> list[dict]:
    """Tenant-scoped lexical search; CJK phrase/substrings use pg_trgm/ILIKE."""
    if not tenant_id or not query or not query.strip() or not (1 <= limit <= 100):
        raise ValueError("Tenant, non-empty query and limit 1..100 are required")
    rows = conn.execute(
        """SELECT memory_id::text,document_text,
                  ts_rank(search_vector,plainto_tsquery('simple',%s)) AS rank
           FROM memory_fts_documents
           WHERE tenant_id=%s
             AND (search_vector @@ plainto_tsquery('simple',%s)
                  OR document_text ILIKE ('%%' || %s || '%%'))
           ORDER BY rank DESC,updated_at DESC,memory_id
           LIMIT %s""",
        (query, tenant_id, query, query, limit),
    ).fetchall()
    return [{"memory_id": row[0], "document_text": row[1],
             "rank": float(row[2])} for row in rows]
