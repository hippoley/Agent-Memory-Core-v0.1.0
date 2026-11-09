"""Optional PostgreSQL storage backend. Requires psycopg 3 and pgvector server extension.

This module does NOT call a hosted memory or embedding API.
AGE graph projection deliberately remains a separate, explicitly optional step.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json

def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(timezone.utc)

class PostgresMemory:
    def __init__(self, dsn: str):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Install the 'postgres' extra: pip install -e '.[postgres]'") from exc
        self.conn = psycopg.connect(dsn)

    def close(self) -> None:
        self.conn.close()

    def migrate(self) -> None:
        sql = (Path(__file__).resolve().parents[3] / "sql/postgresql/001_core.sql").read_text()
        with self.conn.transaction():
            self.conn.execute(sql)

    def remember(self, *, tenant_id: str, subject_id: str, predicate: str, value: dict,
                 source_id: str, valid_from: datetime, valid_to: datetime | None = None,
                 replaces_id: str | None = None, object_sha256: str | None = None) -> str:
        if not all((tenant_id, subject_id, predicate, source_id)):
            raise ValueError("Missing required identity/source")
        valid_from = aware(valid_from)
        valid_to = aware(valid_to) if valid_to else None
        if valid_to is not None and valid_to <= valid_from:
            raise ValueError("Invalid effective interval")
        mid = uuid4()
        with self.conn.transaction():
            if replaces_id is not None:
                # Lock the historical claim to prevent concurrent conflicting corrections.
                old = self.conn.execute(
                    """SELECT id FROM memory_claims WHERE id=%s AND tenant_id=%s
                       AND subject_id=%s AND predicate=%s
                       AND superseded_at IS NULL FOR UPDATE""",
                    (replaces_id, tenant_id,subject_id,predicate)).fetchone()
                if not old:
                    raise ValueError("Missing, mismatched, or already superseded claim")
                self.conn.execute(
                    "UPDATE memory_claims SET superseded_at=clock_timestamp() WHERE id=%s",
                    (replaces_id,))
            self.conn.execute(
                """INSERT INTO memory_claims(id,tenant_id,subject_id,predicate,value,source_id,
                   valid_from,valid_to,replaces_id,object_sha256)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)""",
                (mid,tenant_id,subject_id,predicate,json.dumps(value,ensure_ascii=False),
                 source_id,valid_from,valid_to,replaces_id,object_sha256))
        return str(mid)

    def recall(self, *, tenant_id: str, subject_id: str, predicate: str | None = None,
               valid_at: datetime | None = None, known_at: datetime | None = None) -> list[dict]:
        known_at = aware(known_at or datetime.now(timezone.utc))
        valid_at = aware(valid_at or known_at)
        sql = """SELECT id::text,subject_id,predicate,value,source_id,valid_from,valid_to,
                 recorded_at,superseded_at,object_sha256 FROM memory_claims
                 WHERE tenant_id=%s AND subject_id=%s AND recorded_at<=%s
                 AND (superseded_at IS NULL OR superseded_at>%s)
                 AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)"""
        params = [tenant_id,subject_id,known_at,known_at,valid_at,valid_at]
        if predicate is not None:
            sql += " AND predicate=%s"
            params.append(predicate)
        sql += " ORDER BY recorded_at DESC,id"
        cols = ("id","subject_id","predicate","value","source_id","valid_from","valid_to",
                "recorded_at","superseded_at","object_sha256")
        return [dict(zip(cols,row)) for row in self.conn.execute(sql, params)]

    def store_embedding(self, *, memory_id: str, model_id: str, vector: list[float]) -> None:
        if len(vector)!=384:
            raise ValueError("Prototype embedding profile is 384 dimensions")
        literal = "[" + ",".join(str(float(x)) for x in vector) + "]"
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO memory_embeddings(memory_id,model_id,embedding)
                   VALUES (%s,%s,%s::vector)
                   ON CONFLICT (memory_id,model_id) DO UPDATE SET embedding=EXCLUDED.embedding""",
                (memory_id,model_id,literal))

    def vector_search(self, *, tenant_id: str, model_id: str, vector: list[float],
                      limit: int = 10) -> list[tuple[str,float]]:
        if len(vector)!=384 or not (1<=limit<=100):
            raise ValueError("Invalid vector dimension or limit")
        literal = "[" + ",".join(str(float(x)) for x in vector) + "]"
        # Join before returning any candidate, then enforce tenant filter.
        rows = self.conn.execute(
            """SELECT c.id::text, (e.embedding <=> %s::vector) AS distance
               FROM memory_embeddings e JOIN memory_claims c ON c.id=e.memory_id
               WHERE c.tenant_id=%s AND e.model_id=%s AND c.superseded_at IS NULL
               ORDER BY e.embedding <=> %s::vector LIMIT %s""",
            (literal, tenant_id, model_id, literal, limit))
        return [(r[0],float(r[1])) for r in rows]

    def add_edge(self, *, tenant_id: str, from_entity: str, predicate: str,
                 to_entity: str, source_memory_id: str | None = None) -> str:
        eid = uuid4()
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO memory_edges(edge_id,tenant_id,from_entity,predicate,to_entity,source_memory_id)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (eid,tenant_id,from_entity,predicate,to_entity,source_memory_id))
        return str(eid)

    def outgoing_edges(self, *, tenant_id: str, from_entity: str) -> list[tuple[str,str]]:
        return list(self.conn.execute(
            """SELECT predicate,to_entity FROM memory_edges
               WHERE tenant_id=%s AND from_entity=%s ORDER BY predicate,to_entity""",
            (tenant_id,from_entity)))
