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

class IdempotencyConflict(ValueError):
    pass


class PostgresMemory:
    def __init__(self, dsn: str):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Install the 'postgres' extra: pip install -e '.[postgres]'") from exc
        # Every canonical mutation explicitly uses conn.transaction().
        # Avoid implicit read transactions that could roll back nested writes.
        self.conn = psycopg.connect(dsn, autocommit=True)

    def assert_database_tenant(self, tenant_id: str) -> None:
        """Fail closed unless the login is an RLS-bound, nonprivileged tenant.

        The tenant identity comes from PostgreSQL's authenticated session_user
        through an admin-owned SECURITY DEFINER mapping, not app.tenant_id.
        This does not grant API users an arbitrary SET ROLE capability.
        """
        if not isinstance(tenant_id,str) or not tenant_id:
            raise ValueError("tenant_id required")
        try:
            row=self.conn.execute(
                """SELECT session_user::text,current_user::text,
                          public.memory_authenticated_tenant(),
                          r.rolsuper,r.rolbypassrls,r.rolcreaterole,
                          r.rolcreatedb,pg_has_role(session_user,'memory_tenant','USAGE'),
                          pg_get_userbyid(c.relowner)
                   FROM pg_roles r
                   JOIN pg_class c ON c.oid='public.memory_claims'::regclass
                   WHERE r.rolname=session_user"""
            ).fetchone()
        except Exception as exc:
            raise RuntimeError("Database RLS principal mapping is not installed") from exc
        if (not row or row[0]!=row[1] or row[2]!=tenant_id
                or row[3] or row[4] or row[5] or row[6]
                or not row[7] or row[0]==row[8]):
            raise PermissionError("Database login is not the expected restricted tenant principal")

    def _lock_predicate(self, tenant_id: str, predicate: str) -> None:
        """Serialize policy changes with all writes to this tenant predicate."""
        key="agent-memory-predicate:"+json.dumps(
            [tenant_id,predicate],ensure_ascii=False,separators=(",",":"))
        self.conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(key,))

    def _lock_fact(self, tenant_id: str, subject_id: str, predicate: str) -> None:
        """Serialize competing decisions for one tenant/subject/predicate."""
        key="agent-memory-claim:"+json.dumps(
            [tenant_id,subject_id,predicate],ensure_ascii=False,separators=(",",":"))
        self.conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1))",(key,))

    def predicate_cardinality(self, *, tenant_id: str, predicate: str) -> str:
        """Return the trusted tenant policy; predicates default to single-valued."""
        if not tenant_id or not predicate or not predicate.strip():
            raise ValueError("Tenant and predicate required")
        row=self.conn.execute(
            "SELECT cardinality FROM memory_predicate_policies "
            "WHERE tenant_id=%s AND predicate=%s",(tenant_id,predicate)
        ).fetchone()
        return row[0] if row else "one"

    def set_predicate_cardinality(self, *, tenant_id: str, predicate: str,
                                  cardinality: str) -> None:
        """Set policy before facts exist; extractor/model output cannot change it."""
        if (not tenant_id or not predicate or not predicate.strip()
                or cardinality not in ("one","many")):
            raise ValueError("Invalid tenant predicate policy")
        with self.conn.transaction():
            self._lock_predicate(tenant_id,predicate)
            current=self.predicate_cardinality(tenant_id=tenant_id,predicate=predicate)
            if current!=cardinality and self.conn.execute(
                "SELECT 1 FROM memory_claims WHERE tenant_id=%s AND predicate=%s LIMIT 1",
                (tenant_id,predicate)).fetchone():
                raise ValueError(
                    "Cannot change cardinality for an existing predicate without reconciliation")
            self.conn.execute(
                """INSERT INTO memory_predicate_policies
                   (tenant_id,predicate,cardinality) VALUES (%s,%s,%s)
                   ON CONFLICT (tenant_id,predicate) DO UPDATE SET
                   cardinality=EXCLUDED.cardinality,updated_at=clock_timestamp()""",
                (tenant_id,predicate,cardinality))

    def close(self) -> None:
        self.conn.close()

    def current_time(self) -> datetime:
        """Read the database clock used for recorded_at and bitemporal visibility."""
        return self.conn.execute("SELECT clock_timestamp()").fetchone()[0]

    def migrate(self) -> None:
        """Apply ordered, idempotent SQL migrations shipped with this package."""
        root = resources.files("agent_memory_core").joinpath("resources")
        migrations = sorted(
            (item for item in root.iterdir()
             if item.name.endswith(".sql") and item.name[:3].isdigit()),
            key=lambda item: item.name,
        )
        for migration in migrations:
            sql = migration.read_text(encoding="utf-8")
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
            # Lock order is policy -> fact for every write path. This makes
            # cardinality changes and overlapping writes mutually exclusive.
            self._lock_predicate(tenant_id,predicate)
            self._lock_fact(tenant_id,subject_id,predicate)
            cardinality=self.predicate_cardinality(tenant_id=tenant_id,predicate=predicate)
            if cardinality=="one":
                collisions=self.conn.execute(
                    """SELECT id::text FROM memory_claims
                       WHERE tenant_id=%s AND subject_id=%s AND predicate=%s
                         AND superseded_at IS NULL
                         AND valid_from < coalesce(%s::timestamptz,'infinity'::timestamptz)
                         AND (valid_to IS NULL OR valid_to > %s)
                       FOR UPDATE""",
                    (tenant_id,subject_id,predicate,valid_to,valid_from)).fetchall()
                if replaces_id is not None:
                    if len(collisions)!=1 or collisions[0][0]!=str(replaces_id):
                        raise ValueError(
                            "Single-valued correction must replace the only overlapping claim")
                elif collisions:
                    raise ValueError(
                        "Single-valued predicate already has an overlapping active claim; use replaces_id")
            mutation_time = self.conn.execute("SELECT clock_timestamp()").fetchone()[0]
            if object_sha256 is not None:
                owned = self.conn.execute(
                    "SELECT 1 FROM memory_objects WHERE tenant_id=%s AND object_sha256=%s",
                    (tenant_id,object_sha256)).fetchone()
                if owned is None:
                    raise ValueError("Evidence object is not registered to this tenant")
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
                """INSERT INTO memory_sources(tenant_id,source_id,source_type,trust_level)
                   VALUES (%s,%s,'external','unverified')
                   ON CONFLICT (tenant_id,source_id) DO NOTHING""",
                (tenant_id,source_id))
            self.conn.execute(
                """INSERT INTO memory_claims(id,tenant_id,subject_id,predicate,value,source_id,
                   valid_from,valid_to,replaces_id,object_sha256,recorded_at)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)""",
                (mid,tenant_id,subject_id,predicate,json.dumps(value,ensure_ascii=False),
                 source_id,valid_from,valid_to,replaces_id,object_sha256,mutation_time))
            self.conn.execute(
                """INSERT INTO memory_claim_sources(tenant_id,claim_id,source_id,confidence)
                   VALUES (%s,%s,%s,1.0) ON CONFLICT DO NOTHING""",
                (tenant_id,mid,source_id))
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
            # Fanout tasks are created in the same transaction as the canonical
            # claim and outbox event. A crash cannot leave an event undispatched.
            self.conn.execute(
                """INSERT INTO memory_projection_deliveries(event_id,channel)
                   VALUES (%s,'fts'),(%s,'vector'),(%s,'graph')
                   ON CONFLICT(event_id,channel) DO NOTHING""",
                (mid,mid,mid))
        return str(mid)

    def remember_idempotent(self, *, idempotency_key: str, tenant_id: str,
                            subject_id: str, predicate: str, value: dict,
                            source_id: str, valid_from: datetime,
                            valid_to: datetime | None = None,
                            replaces_id: str | None = None,
                            object_sha256: str | None = None) -> str:
        """One canonical write per (tenant,key,payload), even under races.

        The ledger, canonical claim and outbox commit together. Key reuse with
        a different request is a conflict; never create a second claim.
        """
        import hashlib
        if not isinstance(idempotency_key,str) or not 1<=len(idempotency_key)<=255:
            raise ValueError("Idempotency-Key must contain 1..255 characters")
        request_data={
            "subject_id":subject_id,"predicate":predicate,"value":value,
            "source_id":source_id,"valid_from":aware(valid_from).isoformat(),
            "valid_to":aware(valid_to).isoformat() if valid_to else None,
            "replaces_id":replaces_id,"object_sha256":object_sha256,
        }
        digest=hashlib.sha256(json.dumps(request_data,sort_keys=True,
            ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest()
        with self.conn.transaction():
            # A per-tenant/key transaction advisory lock serializes first-writer
            # creation; hash collisions only reduce concurrency, not isolation.
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (tenant_id+"\\x1f"+idempotency_key,))
            found=self.conn.execute(
                """SELECT request_sha256,result FROM memory_idempotency
                   WHERE tenant_id=%s AND idempotency_key=%s""",
                (tenant_id,idempotency_key)).fetchone()
            if found is not None:
                if found[0]!=digest:
                    raise IdempotencyConflict("Idempotency key reused with a different request")
                return found[1]["id"]
            mid=self.remember(tenant_id=tenant_id,subject_id=subject_id,
                predicate=predicate,value=value,source_id=source_id,
                valid_from=valid_from,valid_to=valid_to,
                replaces_id=replaces_id,object_sha256=object_sha256)
            self.conn.execute(
                """INSERT INTO memory_idempotency
                   (tenant_id,idempotency_key,request_sha256,result)
                   VALUES (%s,%s,%s,%s::jsonb)""",
                (tenant_id,idempotency_key,digest,json.dumps({"id":mid})))
            return mid

    def apply_candidate(self, *, tenant_id: str, subject_id: str, predicate: str,
                        value: dict, source_id: str, confidence: float = 1.0,
                        merge: bool = False):
        """Atomically classify, deduplicate, consolidate or version one candidate.

        Concurrent processors for the same tenant/subject/predicate serialize
        only their own key. Claim, provenance, outbox and projection fanout
        commit together; exact duplicates add provenance without another event.

        Returns (memory_id, replaces_id, duplicate, relation) where relation is
        one of CREATED / DUPLICATE / SUPERSEDES / CONTRADICTION / MERGED.
        merge=True consolidates the candidate into the single active claim when
        the values are mergeable (recursive dict merge, list union); scalar
        conflicts are never silently merged — they classify as CONTRADICTION
        and take the versioned-replacement path.
        """
        from .processing import (deep_merge, resolve_conflicts, classify_relation,
                                 RELATION_CREATED, RELATION_DUPLICATE,
                                 RELATION_CONTRADICTION, RELATION_MERGED,
                                 RELATION_SUPERSEDES)
        if not tenant_id or not subject_id or not predicate or not source_id:
            raise ValueError("Missing candidate identity or provenance")
        canonical=json.dumps(value,ensure_ascii=False,sort_keys=True,
                             separators=(",",":"),allow_nan=False)
        with self.conn.transaction():
            self._lock_predicate(tenant_id,predicate)
            self._lock_fact(tenant_id,subject_id,predicate)
            cardinality=self.predicate_cardinality(tenant_id=tenant_id,predicate=predicate)
            now=self.current_time()
            active=self.recall(tenant_id=tenant_id,subject_id=subject_id,
                predicate=predicate,valid_at=now,known_at=now)
            exact=next((row for row in active
                if json.dumps(row["value"],ensure_ascii=False,sort_keys=True,
                              separators=(",",":"),allow_nan=False)==canonical),None)
            if exact is not None:
                self.link_claim_source(tenant_id=tenant_id,memory_id=exact["id"],
                    source_id=source_id,confidence=float(confidence))
                return exact["id"],None,True,RELATION_DUPLICATE
            if cardinality=="many":
                memory_id=self.remember(tenant_id=tenant_id,subject_id=subject_id,
                    predicate=predicate,value=value,source_id=source_id,valid_from=now)
                return memory_id,None,False,RELATION_CREATED
            if len(active)>1:
                raise ValueError(
                    "Single-valued predicate has multiple active claims; reconcile before processing")
            if not active:
                memory_id=self.remember(tenant_id=tenant_id,subject_id=subject_id,
                    predicate=predicate,value=value,source_id=source_id,
                    valid_from=now)
                return memory_id,None,False,RELATION_CREATED
            target=active[0]
            relation=classify_relation(value,target["value"])
            if merge and relation==RELATION_MERGED:
                try:
                    merged=deep_merge(target["value"],value)
                except TypeError:
                    merged=None
                if merged is not None:
                    memory_id=self.remember(tenant_id=tenant_id,subject_id=subject_id,
                        predicate=predicate,value=merged,source_id=source_id,
                        valid_from=now,replaces_id=target["id"])
                    return memory_id,target["id"],False,RELATION_MERGED
            if relation==RELATION_DUPLICATE:
                # Subsumed candidate: the active claim already encodes it.
                self.link_claim_source(tenant_id=tenant_id,memory_id=target["id"],
                    source_id=source_id,confidence=float(confidence))
                return target["id"],None,True,RELATION_DUPLICATE
            if relation==RELATION_CONTRADICTION:
                # Resolve scalar conflicts in favor of the newer candidate
                # while preserving non-conflicting knowledge.
                try:
                    resolved=resolve_conflicts(target["value"],value)
                except TypeError:
                    resolved=value
                memory_id=self.remember(tenant_id=tenant_id,subject_id=subject_id,
                    predicate=predicate,value=resolved,source_id=source_id,
                    valid_from=now,replaces_id=target["id"])
                return memory_id,target["id"],False,RELATION_CONTRADICTION
            memory_id=self.remember(tenant_id=tenant_id,subject_id=subject_id,
                predicate=predicate,value=value,source_id=source_id,
                valid_from=now,replaces_id=target["id"])
            return memory_id,target["id"],False,RELATION_SUPERSEDES

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

    def list_claims(self, *, tenant_id: str, subject_id: str | None = None,
                    predicate: str | None = None, include_superseded: bool = False,
                    valid_at: datetime | None = None, known_at: datetime | None = None,
                    limit: int = 100, offset: int = 0) -> list[dict]:
        """Browse canonical claims for one tenant, newest first.

        This is the read path behind consoles: it never invents facts, it only
        exposes rows the store already holds. ``known_at`` gives the "what did
        we believe then" view; ``include_superseded`` keeps replaced or
        forgotten claims visible so the audit trail is browsable.
        """
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if offset < 0:
            raise ValueError("offset must be zero or positive")
        known_at = aware(known_at) if known_at is not None else None
        valid_at = aware(valid_at) if valid_at is not None else None
        sql = """SELECT id::text,subject_id,predicate,value,source_id,valid_from,valid_to,
                 recorded_at,superseded_at,object_sha256 FROM memory_claims
                 WHERE tenant_id=%s"""
        params: list = [tenant_id]
        if subject_id is not None:
            sql += " AND subject_id=%s"
            params.append(subject_id)
        if predicate is not None:
            sql += " AND predicate=%s"
            params.append(predicate)
        if known_at is not None:
            sql += " AND recorded_at<=%s"
            params.append(known_at)
        if not include_superseded:
            sql += " AND superseded_at IS NULL"
            if known_at is not None:
                sql += " AND superseded_at>%s"
                params.append(known_at)
        if valid_at is not None:
            sql += " AND valid_from<=%s AND (valid_to IS NULL OR valid_to>%s)"
            params.extend([valid_at, valid_at])
        sql += " ORDER BY recorded_at DESC,id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        cols = ("id","subject_id","predicate","value","source_id","valid_from","valid_to",
                "recorded_at","superseded_at","object_sha256")
        return [dict(zip(cols,row)) for row in self.conn.execute(sql, params)]

    def forget(self, *, tenant_id: str, memory_id: str) -> bool:
        """Withdraw belief in one claim (soft forget; audit rows are retained).

        The claim is marked superseded at the current mutation time, so it
        disappears from all active reads while the bitemporal history stays
        queryable via known_at. Emits a durable memory.claim.forgotten event
        and outbox record in the same transaction. Returns False when the
        claim does not exist for the tenant or is already forgotten.
        """
        if not tenant_id or not memory_id:
            raise ValueError("Tenant and memory id are required")
        with self.conn.transaction():
            row=self.conn.execute(
                """SELECT subject_id,predicate FROM memory_claims
                   WHERE id=%s::uuid AND tenant_id=%s AND superseded_at IS NULL
                   FOR UPDATE""",
                (memory_id,tenant_id)).fetchone()
            if row is None:
                return False
            subject_id,predicate=row
            mutation_time=self.conn.execute("SELECT clock_timestamp()").fetchone()[0]
            self.conn.execute(
                "UPDATE memory_claims SET superseded_at=%s WHERE id=%s",
                (mutation_time,memory_id))
            packed=json.dumps({"memory_id":str(memory_id),"subject_id":subject_id,
                               "predicate":predicate,"reason":"explicit_forget"},
                              ensure_ascii=False,sort_keys=True)
            event_id=str(uuid4())
            self.conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'memory.claim.forgotten',%s::jsonb)""",
                (tenant_id,event_id,packed))
            self.conn.execute(
                """INSERT INTO memory_outbox
                   (event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'claim',%s,'memory.claim.forgotten',%s::jsonb)""",
                (event_id,tenant_id,str(memory_id),packed))
            return True

    def forget_many(self, *, tenant_id: str, subject_id: str | None = None,
                    predicate: str | None = None) -> list[str]:
        """Soft-forget every active claim matching the tenant-scoped filters.

        At least one filter must be supplied. Each forgotten claim emits its
        own durable forgotten event. Returns the forgotten claim ids.
        """
        if not tenant_id or (not subject_id and not predicate):
            raise ValueError("Provide subject_id and/or predicate to forget")
        with self.conn.transaction():
            sql="""SELECT id::text FROM memory_claims
                   WHERE tenant_id=%s AND superseded_at IS NULL"""
            params=[tenant_id]
            if subject_id:
                sql+=" AND subject_id=%s"; params.append(subject_id)
            if predicate:
                sql+=" AND predicate=%s"; params.append(predicate)
            sql+=" FOR UPDATE"
            ids=[row[0] for row in self.conn.execute(sql,params)]
        forgotten=[]
        for mid in ids:
            if self.forget(tenant_id=tenant_id,memory_id=mid):
                forgotten.append(mid)
        return forgotten

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
                    WHERE id=%s::uuid AND tenant_id=%s
                    ON CONFLICT (memory_id,model_id)
                    DO UPDATE SET embedding=EXCLUDED.embedding""",
                (model_id,literal,memory_id,tenant_id))
            if result.rowcount != 1:
                raise PermissionError("Memory does not belong to requested tenant")

    def fulltext_search(self, *, tenant_id: str, query: str, limit: int = 50,
                        valid_at: datetime | None = None,
                        known_at: datetime | None = None) -> list[str]:
        """Ranked FTS projection with canonical tenant and bitemporal filtering.

        The simple dictionary is language-neutral but NOT a Chinese word
        segmenter. Hybrid retrieval retains literal SQL fallback.
        """
        if not tenant_id or not query or not query.strip() or not 1 <= limit <= 100:
            raise ValueError("Invalid fulltext search parameters")
        known_at=aware(known_at or datetime.now(timezone.utc))
        valid_at=aware(valid_at or known_at)
        rows=self.conn.execute(
            """SELECT s.memory_id::text
               FROM memory_claim_search s
               JOIN memory_claims c ON c.id=s.memory_id
               WHERE c.tenant_id=%s AND s.tenant_id=%s
                 AND s.document @@ websearch_to_tsquery('simple',%s)
                 AND c.recorded_at<=%s
                 AND (c.superseded_at IS NULL OR c.superseded_at>%s)
                 AND c.valid_from<=%s AND (c.valid_to IS NULL OR c.valid_to>%s)
               ORDER BY ts_rank_cd(s.document,websearch_to_tsquery('simple',%s))
                        DESC,c.recorded_at DESC,s.memory_id
               LIMIT %s""",
            (tenant_id,tenant_id,query,known_at,known_at,valid_at,valid_at,query,limit))
        return [row[0] for row in rows]

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

    def link_claim_source(self, *, tenant_id: str, memory_id: str,
                          source_id: str, confidence: float = 1.0) -> bool:
        if (not tenant_id or not memory_id or not source_id
                or not 0.0 <= confidence <= 1.0):
            raise ValueError("Invalid claim source link")
        with self.conn.transaction():
            claim = self.conn.execute(
                "SELECT 1 FROM memory_claims WHERE tenant_id=%s AND id=%s",
                (tenant_id,memory_id)).fetchone()
            if not claim:
                raise PermissionError("Memory does not belong to requested tenant")
            self.conn.execute(
                """INSERT INTO memory_sources(tenant_id,source_id,source_type,trust_level)
                   VALUES (%s,%s,'external','unverified')
                   ON CONFLICT (tenant_id,source_id) DO NOTHING""",
                (tenant_id,source_id))
            result = self.conn.execute(
                """INSERT INTO memory_claim_sources(tenant_id,claim_id,source_id,confidence)
                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (tenant_id,memory_id,source_id,confidence))
        return result.rowcount == 1

    def sources_for_memory(self, *, tenant_id: str, memory_id: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT s.source_id,s.source_type,s.uri,s.object_sha256,s.trust_level,
                      s.metadata,cs.confidence,cs.linked_at
               FROM memory_claim_sources cs
               JOIN memory_sources s USING (tenant_id,source_id)
               WHERE cs.tenant_id=%s AND cs.claim_id=%s
               ORDER BY cs.linked_at,s.source_id""",
            (tenant_id,memory_id))
        columns=("source_id","source_type","uri","object_sha256",
                 "trust_level","metadata","confidence","linked_at")
        return [dict(zip(columns,row)) for row in rows]

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
            payload=json.dumps({"edge_id":str(eid)},sort_keys=True)
            self.conn.execute(
                """INSERT INTO memory_events(tenant_id,event_id,event_type,payload)
                   VALUES (%s,%s,'memory.edge.created',%s::jsonb)""",
                (tenant_id,str(eid),payload))
            self.conn.execute(
                """INSERT INTO memory_outbox(event_id,tenant_id,aggregate_type,aggregate_id,event_type,payload)
                   VALUES (%s,%s,'edge',%s,'memory.edge.created',%s::jsonb)""",
                (eid,tenant_id,str(eid),payload))
            self.conn.execute(
                """INSERT INTO memory_projection_deliveries(event_id,channel)
                   VALUES (%s,'graph') ON CONFLICT DO NOTHING""",(eid,))
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
                     property: str, limit: int = 100,
                     start_at: datetime | None = None,
                     end_at: datetime | None = None) -> list[dict]:
        """Physical event time ordering is distinct from ingest time."""
        if not all((tenant_id,device_id,property)) or not 1<=limit<=10000:
            raise ValueError("Invalid observation query")
        start_at=aware(start_at) if start_at else None
        end_at=aware(end_at) if end_at else None
        if start_at and end_at and end_at <= start_at:
            raise ValueError("end_at must be later than start_at")
        sql = """SELECT source_event_id,observed_at,ingested_at,measured_value,unit,source_id
                 FROM memory_observations
                 WHERE tenant_id=%s AND device_id=%s AND property=%s"""
        params=[tenant_id,device_id,property]
        if start_at:
            sql += " AND observed_at >= %s"
            params.append(start_at)
        if end_at:
            sql += " AND observed_at < %s"
            params.append(end_at)
        sql += " ORDER BY observed_at DESC,source_event_id DESC LIMIT %s"
        params.append(limit)
        columns=("source_event_id","observed_at","ingested_at",
                 "measured_value","unit","source_id")
        rows=self.conn.execute(sql,params)
        return [dict(zip(columns,row)) for row in rows]

    def aggregate_observations(self, *, tenant_id: str, device_id: str,
                               property: str, start_at: datetime,
                               end_at: datetime) -> dict:
        if not all((tenant_id,device_id,property)):
            raise ValueError("Tenant, device and property are required")
        start_at=aware(start_at)
        end_at=aware(end_at)
        if end_at <= start_at:
            raise ValueError("end_at must be later than start_at")
        row=self.conn.execute(
            """SELECT count(*),min(measured_value),max(measured_value),
                      avg(measured_value)
               FROM memory_observations
               WHERE tenant_id=%s AND device_id=%s AND property=%s
                 AND observed_at >= %s AND observed_at < %s""",
            (tenant_id,device_id,property,start_at,end_at)).fetchone()
        return {"count":row[0],"min":row[1],"max":row[2],"avg":float(row[3]) if row[3] is not None else None,
                "start_at":start_at,"end_at":end_at}

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


    def register_object(self, *, tenant_id: str, digest: str,
                        byte_size: int, content_type: str = "application/octet-stream") -> None:
        """Grant one tenant access to a verified content-addressed object."""
        import re
        if (not tenant_id or not re.fullmatch(r"[0-9a-f]{64}",digest)
                or byte_size < 0 or not content_type):
            raise ValueError("Invalid tenant, object digest, size or content type")
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO memory_objects(tenant_id,object_sha256,byte_size,content_type)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (tenant_id,object_sha256) DO UPDATE SET
                     byte_size=EXCLUDED.byte_size,
                     content_type=EXCLUDED.content_type""",
                (tenant_id,digest,byte_size,content_type))

    def object_accessible(self, *, tenant_id: str, digest: str) -> bool:
        """Check tenant ownership before reading a shared object namespace."""
        if not tenant_id or not digest:
            return False
        return self.conn.execute(
            "SELECT 1 FROM memory_objects WHERE tenant_id=%s AND object_sha256=%s",
            (tenant_id,digest)).fetchone() is not None
