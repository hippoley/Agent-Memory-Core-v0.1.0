"""Optional PostgreSQL storage backend. Requires psycopg 3 and pgvector server extension.

This module does NOT call a hosted memory or embedding API.
AGE graph projection deliberately remains a separate, explicitly optional step.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from .vector_validation import vector_literal
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
        sql = resources.files("agent_memory_core").joinpath("resources/001_core.sql").read_text(encoding="utf-8")
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
            mutation_time = self.conn.execute("SELECT clock_timestamp()").fetchone()[0]
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
                    "UPDATE memory_claims SET superseded_at=%s WHERE id=%s",
                    (mutation_time,replaces_id,))
            self.conn.execute(
                """INSERT INTO memory_claims(id,tenant_id,subject_id,predicate,value,source_id,
                   valid_from,valid_to,replaces_id,object_sha256,recorded_at)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)""",
                (mid,tenant_id,subject_id,predicate,json.dumps(value,ensure_ascii=False),
                 source_id,valid_from,valid_to,replaces_id,object_sha256,mutation_time))
            event_payload = {"memory_id": str(mid), "subject_id": subject_id,
                             "predicate": predicate, "source_id": source_id}
            packed = json.dumps(event_payload, ensure_ascii=False, sort_keys=True)
            self.conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'memory.claim.created',%s::jsonb)""",
                (tenant_id,str(mid),packed))
            self.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim',%s,'memory.claim.created',%s::jsonb)""",
                (mid,tenant_id,str(mid),packed))
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

    @staticmethod
    def _embedding_table(dimension: int) -> str:
        # Table identifiers are deliberately chosen from an internal whitelist.
        if dimension == 384:
            return "memory_embeddings"
        if dimension == 512:
            return "memory_embeddings_512"
        raise ValueError("Unsupported embedding dimension")

    def store_embedding(self, *, tenant_id: str, memory_id: str,
                        model_id: str, vector: list[float]) -> None:
        if not tenant_id or not model_id:
            raise ValueError("Tenant and embedding model required")
        literal = vector_literal(vector)
        dimension = len(vector)
        table = self._embedding_table(dimension)
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO memory_embedding_models(tenant_id,model_id,dimensions)
                   VALUES (%s,%s,%s) ON CONFLICT (tenant_id,model_id) DO NOTHING""",
                (tenant_id,model_id,dimension))
            known_dimension = self.conn.execute(
                """SELECT dimensions FROM memory_embedding_models
                   WHERE tenant_id=%s AND model_id=%s FOR UPDATE""",
                (tenant_id,model_id)).fetchone()[0]
            if known_dimension != dimension:
                raise ValueError("Embedding model ID is already bound to another dimension")
            result = self.conn.execute(
                f"""INSERT INTO {table}(memory_id,model_id,embedding)
                    SELECT id,%s,%s::vector FROM memory_claims
                    WHERE id=%s AND tenant_id=%s
                    ON CONFLICT (memory_id,model_id)
                    DO UPDATE SET embedding=EXCLUDED.embedding""",
                (model_id,literal,memory_id,tenant_id))
            if result.rowcount != 1:
                raise PermissionError("Memory does not belong to requested tenant")

    def vector_search(self, *, tenant_id: str, model_id: str, vector: list[float],
                      limit: int = 10, valid_at: datetime | None = None,
                      known_at: datetime | None = None) -> list[tuple[str,float]]:
        """Exact, tenant- and bitemporal-scoped cosine search.

        Legacy 384-dimensional models and Chinese 512-dimensional models use
        separate tables/ANN indexes; the model registry prevents accidental
        mixing of vector dimensions under one tenant/model ID.
        """
        if not tenant_id or not model_id or not (1 <= limit <= 100):
            raise ValueError("Invalid tenant, embedding model or limit")
        literal = vector_literal(vector)
        dimension=len(vector)
        table=self._embedding_table(dimension)
        known_at=aware(known_at or datetime.now(timezone.utc))
        valid_at=aware(valid_at or known_at)
        profile=self.conn.execute(
            """SELECT dimensions FROM memory_embedding_models
               WHERE tenant_id=%s AND model_id=%s""",
            (tenant_id,model_id)).fetchone()
        if profile is None:
            return []
        if profile[0] != dimension:
            raise ValueError("Query dimension disagrees with registered embedding model")
        rows=self.conn.execute(
            f"""WITH scoped AS MATERIALIZED (
                 SELECT e.memory_id,e.embedding FROM {table} e
                 JOIN memory_claims c ON c.id=e.memory_id
                 WHERE c.tenant_id=%s AND e.model_id=%s
                   AND c.recorded_at<=%s
                   AND (c.superseded_at IS NULL OR c.superseded_at>%s)
                   AND c.valid_from<=%s
                   AND (c.valid_to IS NULL OR c.valid_to>%s)
               )
               SELECT memory_id::text, embedding <=> %s::vector AS distance
               FROM scoped ORDER BY distance,memory_id LIMIT %s""",
            (tenant_id,model_id,known_at,known_at,valid_at,valid_at,literal,limit))
        return [(r[0],float(r[1])) for r in rows]

    def upsert_entity(self, *, tenant_id: str, entity_id: str,
                      entity_type: str, canonical_name: str,
                      aliases: list[str] | None = None,
                      attributes: dict | None = None) -> None:
        allowed = {"person","agent","organization","project","device",
                   "room","place","concept","account","document"}
        if (not tenant_id or not entity_id or not canonical_name.strip()
                or entity_type not in allowed):
            raise ValueError("Invalid tenant, entity ID, type or canonical name")
        alias_values = sorted({a.strip() for a in (aliases or [])
                               if isinstance(a,str) and a.strip()})
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO memory_entities
                   (tenant_id,entity_id,entity_type,canonical_name,aliases,attributes)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (tenant_id,entity_id) DO UPDATE SET
                     entity_type=EXCLUDED.entity_type,
                     canonical_name=EXCLUDED.canonical_name,
                     aliases=EXCLUDED.aliases,
                     attributes=EXCLUDED.attributes""",
                (tenant_id,entity_id,entity_type,canonical_name,
                 alias_values,json.dumps(attributes or {},ensure_ascii=False)))

    def resolve_entity(self, *, tenant_id: str, name: str) -> dict | None:
        """Resolve a canonical name or alias; never guess when ambiguous."""
        if not tenant_id or not name or not name.strip():
            raise ValueError("Tenant and entity name are required")
        rows = self.conn.execute(
            """SELECT entity_id,entity_type,canonical_name,aliases,attributes
               FROM memory_entities
               WHERE tenant_id=%s AND
                 (lower(canonical_name)=lower(%s) OR EXISTS
                   (SELECT 1 FROM unnest(aliases) alias
                    WHERE lower(alias)=lower(%s)))
               ORDER BY entity_id LIMIT 2""",
            (tenant_id,name.strip(),name.strip())).fetchall()
        if len(rows) > 1:
            raise ValueError("Ambiguous entity alias; explicit entity_id required")
        if not rows:
            return None
        row=rows[0]
        return {"entity_id":row[0],"entity_type":row[1],
                "canonical_name":row[2],"aliases":row[3],"attributes":row[4]}

    def add_edge(self, *, tenant_id: str, from_entity: str, predicate: str,
                 to_entity: str, source_memory_id: str | None = None) -> str:
        if not all((tenant_id,from_entity,predicate,to_entity)):
            raise ValueError("Tenant, endpoints and predicate required")
        eid = uuid4()
        with self.conn.transaction():
            if source_memory_id is not None:
                found = self.conn.execute(
                    "SELECT 1 FROM memory_claims WHERE id=%s AND tenant_id=%s",
                    (source_memory_id,tenant_id)).fetchone()
                if not found:
                    raise PermissionError("Source memory belongs to another tenant or does not exist")
            self.conn.execute(
                """INSERT INTO memory_edges(edge_id,tenant_id,from_entity,predicate,to_entity,source_memory_id)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (eid,tenant_id,from_entity,predicate,to_entity,source_memory_id))
        return str(eid)


    def retire_edge(self, *, tenant_id: str, edge_id: str) -> bool:
        """Logically retire an edge; AGE projection reconciliation is separate.

        No physical graph deletion happens here. Call AgeProjection.reconcile_invalidated
        after this and whenever sourced memories are superseded.
        """
        if not tenant_id or not edge_id:
            raise ValueError("Tenant and edge ID are required")
        with self.conn.transaction():
            row = self.conn.execute(
                """UPDATE memory_edges SET retired_at=clock_timestamp()
                   WHERE tenant_id=%s AND edge_id=%s AND retired_at IS NULL
                   RETURNING edge_id""", (tenant_id,edge_id)).fetchone()
        return row is not None

    def record_observation(self, *, tenant_id: str, device_id: str,
                           source_event_id: str, observed_at: datetime,
                           property: str, value: float, unit: str,
                           source_id: str) -> bool:
        """Store a timestamped observation without confusing event and arrival times.

        Source event ID is tenant/device scoped. Retried identical observations
        are idempotent, conflicting repeats are rejected.
        """
        import math
        if not all((tenant_id,device_id,source_event_id,property,unit,source_id)):
            raise ValueError("Tenant, device, event, property, unit and source are required")
        if not math.isfinite(float(value)):
            raise ValueError("Observed value must be finite")
        observed_at = aware(observed_at)
        with self.conn.transaction():
            inserted = self.conn.execute(
                """INSERT INTO memory_observations
                   (tenant_id,device_id,source_event_id,observed_at,
                    measured_value,unit,property,source_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(tenant_id,device_id,source_event_id) DO NOTHING
                   RETURNING source_event_id""",
                (tenant_id,device_id,source_event_id,observed_at,
                 float(value),unit,property,source_id),
            ).fetchone()
            if inserted:
                event_id = uuid4()
                payload = {"device_id": device_id, "source_event_id": source_event_id,
                           "observed_at": observed_at.isoformat(), "property": property,
                           "value": float(value), "unit": unit, "source_id": source_id}
                packed = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                self.conn.execute(
                    """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                       VALUES (%s,%s,'memory.observation.recorded',%s::jsonb)""",
                    (tenant_id,str(event_id),packed))
                self.conn.execute(
                    """INSERT INTO memory_outbox
                       (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                       VALUES (%s,%s,'device',%s,'memory.observation.recorded',%s::jsonb)""",
                    (event_id,tenant_id,device_id,packed))
                return True
            prior = self.conn.execute(
                """SELECT observed_at,measured_value,unit,property,source_id
                   FROM memory_observations
                   WHERE tenant_id=%s AND device_id=%s AND source_event_id=%s""",
                (tenant_id,device_id,source_event_id),
            ).fetchone()
            if prior != (observed_at,float(value),unit,property,source_id):
                raise ValueError("Conflicting observation with same source event ID")
            return False

    def observations(self, *, tenant_id: str, device_id: str,
                     property: str, limit: int = 100) -> list[dict]:
        """Physical event time ordering is distinct from ingest time."""
        if not all((tenant_id,device_id,property)) or not 1<=limit<=10000:
            raise ValueError("Invalid observation query")
        columns=("source_event_id","observed_at","ingested_at",
                 "measured_value","unit","source_id")
        rows = self.conn.execute(
            """SELECT source_event_id,observed_at,ingested_at,measured_value,unit,source_id
               FROM memory_observations
               WHERE tenant_id=%s AND device_id=%s AND property=%s
               ORDER BY observed_at DESC,source_event_id DESC LIMIT %s""",
            (tenant_id,device_id,property,limit),
        )
        return [dict(zip(columns,row)) for row in rows]

    def events_after(self, *, tenant_id: str, after: str | None = None,
                     limit: int = 100) -> list[dict]:
        """Read a stable tenant-scoped event page after an opaque event-ID cursor."""
        if not tenant_id or not 1 <= limit <= 500:
            raise ValueError("Invalid tenant or event page size")
        if after is None:
            rows = self.conn.execute(
                """SELECT event_id,event_type,payload,recorded_at
                   FROM memory_events WHERE tenant_id=%s
                   ORDER BY recorded_at,event_id LIMIT %s""",
                (tenant_id,limit))
        else:
            cursor = self.conn.execute(
                """SELECT recorded_at,event_id FROM memory_events
                   WHERE tenant_id=%s AND event_id=%s""", (tenant_id,after)).fetchone()
            if cursor is None:
                raise ValueError("Unknown event cursor for this tenant")
            rows = self.conn.execute(
                """SELECT event_id,event_type,payload,recorded_at FROM memory_events
                   WHERE tenant_id=%s AND
                     (recorded_at,event_id)>(%s,%s)
                   ORDER BY recorded_at,event_id LIMIT %s""",
                (tenant_id,cursor[0],cursor[1],limit))
        return [{"event_id":str(row[0]),"event_type":row[1],
                 "payload":row[2],"recorded_at":row[3]} for row in rows]

    def outgoing_edges(self, *, tenant_id: str, from_entity: str) -> list[tuple[str,str]]:
        return list(self.conn.execute(
            """SELECT predicate,to_entity FROM memory_edges
               WHERE tenant_id=%s AND from_entity=%s AND retired_at IS NULL ORDER BY predicate,to_entity""",
            (tenant_id,from_entity)))
