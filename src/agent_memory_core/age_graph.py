"""Apache AGE projection from canonical PostgreSQL memory_edges.

Graph writes and projection ledger commit atomically in the SAME PostgreSQL database.
Graph is a rebuildable index, NOT authoritative memory data.

Requires a PostgreSQL server with AGE installed and initialized. The projection is
run explicitly, never automatically from remember/add_edge.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

try:
    from psycopg import sql
except ImportError:
    sql = None


GRAPH_NAME = "memory_graph"


def _cypher_string(value: str) -> str:
    """Escape Cypher string values (not SQL); SQL text is quoted separately."""
    if not isinstance(value, str):
        raise TypeError("Cypher properties must be strings")
    # JSON string literals use compatible escaped double-quoted forms for AGE Cypher.
    return json.dumps(value, ensure_ascii=True)


def edge_upsert_cypher(*, tenant_id: str, edge_id: str, from_entity: str,
                       predicate: str, to_entity: str) -> str:
    """Build an AGE MERGE statement with fixed labels and escaped properties."""
    src_key = _cypher_string(tenant_id + "\x1f" + from_entity)
    dst_key = _cypher_string(tenant_id + "\x1f" + to_entity)
    eid = _cypher_string(edge_id)
    src = _cypher_string(from_entity)
    dst = _cypher_string(to_entity)
    tenant = _cypher_string(tenant_id)
    relation = _cypher_string(predicate)
    return (
        f"MERGE (a:MemoryEntity {{key: {src_key}}}) "
        f"MERGE (b:MemoryEntity {{key: {dst_key}}}) "
        f"MERGE (a)-[r:MEMORY_REL {{edge_id: {eid}}}]->(b) "
        f"SET a.tenant_id = {tenant}, a.entity_id = {src}, "
        f"b.tenant_id = {tenant}, b.entity_id = {dst}, "
        f"r.predicate = {relation}, r.tenant_id = {tenant} "
        "RETURN r"
    )




def claim_upsert_cypher(*, tenant_id: str, claim_id: str, subject_id: str,
                        predicate: str, polarity: str, value_json: str,
                        source_id: str) -> str:
    """Build a fixed-label AGE claim node MERGE with escaped scalar properties."""
    if polarity not in {"positive", "negative"}:
        raise ValueError("Unsupported assertion polarity")
    key = _cypher_string(tenant_id + "\x1f" + claim_id)
    tenant = _cypher_string(tenant_id)
    cid = _cypher_string(claim_id)
    subject = _cypher_string(subject_id)
    relation = _cypher_string(predicate)
    stance = _cypher_string(polarity)
    value = _cypher_string(value_json)
    source = _cypher_string(source_id)
    return (
        f"MERGE (c:MemoryClaim {{key: {key}}}) "
        f"SET c.tenant_id={tenant}, c.claim_id={cid}, "
        f"c.subject_id={subject}, c.predicate={relation}, "
        f"c.polarity={stance}, c.value_json={value}, c.source_id={source} "
        "RETURN c"
    )

def edge_delete_cypher(*, tenant_id: str, edge_id: str) -> str:
    """Delete only the typed edge addressed by tenant and canonical edge ID."""
    tenant=_cypher_string(tenant_id)
    eid=_cypher_string(edge_id)
    return (
        f"MATCH ()-[r:MEMORY_REL {{edge_id: {eid}, tenant_id: {tenant}}}]->() "
        "DELETE r RETURN r"
    )


def graph_neighbors_cypher(*, tenant_id: str, from_entity: str, hops: int = 1) -> str:
    """Traversal with bounded, validated hop count; no user-controlled label names."""
    if hops not in (1, 2):
        raise ValueError("Only 1 or 2 hops supported by current contract")
    key = _cypher_string(tenant_id + "\x1f" + from_entity)
    path = "-[:MEMORY_REL]->(b:MemoryEntity)" if hops == 1 else (
        "-[:MEMORY_REL]->(:MemoryEntity)-[:MEMORY_REL]->(b:MemoryEntity)"
    )
    return f"MATCH (a:MemoryEntity {{key: {key}}}){path} RETURN DISTINCT b.entity_id"


@dataclass(frozen=True)
class ProjectionProgress:
    attempted: int
    projected: int


class AgeProjection:
    """Bounded, restart-safe graph projection from SQL facts.

    Security: uses tenant-qualified entity keys in graph. Direct AGE queries
    must only be exposed to trusted service operators. Application requests
    go through this tenant-scoped wrapper; no DB RLS is claimed.
    """
    def __init__(self, conn):
        if sql is None:
            raise RuntimeError("Install psycopg[binary] to use AGE")
        self.conn = conn

    def initialize(self) -> None:
        """Initialize AGE and graph, without failing if the graph exists."""
        with self.conn.transaction():
            self.conn.execute("CREATE EXTENSION IF NOT EXISTS age")
            self.conn.execute("LOAD 'age'")
            self.conn.execute("SET search_path TO ag_catalog, public")
            exists = self.conn.execute(
                "SELECT 1 FROM ag_catalog.ag_graph WHERE name=%s",
                (GRAPH_NAME,),
            ).fetchone()
            if not exists:
                self.conn.execute("SELECT ag_catalog.create_graph(%s)", (GRAPH_NAME,))
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_graph_projection (
                    edge_id uuid PRIMARY KEY REFERENCES memory_edges(edge_id)
                        ON DELETE CASCADE,
                    projected_at timestamptz NOT NULL DEFAULT clock_timestamp()
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_graph_claim_projection (
                    claim_id uuid PRIMARY KEY REFERENCES memory_claims(id)
                        ON DELETE CASCADE,
                    tenant_id text NOT NULL,
                    projected_at timestamptz NOT NULL DEFAULT clock_timestamp()
                )
            """)

    def _cypher(self, statement: str) -> list[tuple]:
        """AGE requires Cypher query text as a SQL literal, not bind placeholder."""
        # AGE's parser specifically requires dollar-quoted Cypher query text.
        # psycopg.sql.Literal produces a single-quoted string and is rejected.
        # Choose a delimiter absent from the validated Cypher statement so
        # input cannot terminate the SQL dollar-quoted literal.
        suffix = "agecypher"
        while f"${suffix}$" in statement:
            suffix += "x"
        token = f"${suffix}$"
        query = sql.SQL(
            "SELECT * FROM ag_catalog.cypher({}, " + token
        ).format(sql.Literal(GRAPH_NAME)) + sql.SQL(statement) + sql.SQL(
            token + ") AS (result ag_catalog.agtype)"
        )
        return list(self.conn.execute(query))


    def project_claim_event(self, conn, tenant_id: str, event_id: str,
                            aggregate_id: str, payload) -> None:
        """Idempotently project one canonical claim event to a MemoryClaim node.

        Call initialize() once for each worker connection before consuming AGE
        jobs. The job acknowledgement and this graph+ledger write share the
        caller's ProjectionWorker transaction.
        """
        if isinstance(payload, str):
            payload = json.loads(payload)
        claim_id = payload.get("memory_id") if isinstance(payload, dict) else None
        if not claim_id:
            raise ValueError("Claim projection event is missing memory_id")
        row = conn.execute(
            """SELECT subject_id,predicate,value,source_id,polarity
               FROM memory_claims WHERE tenant_id=%s AND id=%s::uuid""",
            (tenant_id, claim_id),
        ).fetchone()
        if row is None:
            raise LookupError("Canonical claim for AGE projection was not found")
        subject_id, predicate, value, source_id, polarity = row
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
        self._cypher(claim_upsert_cypher(
            tenant_id=tenant_id, claim_id=claim_id, subject_id=subject_id,
            predicate=predicate, polarity=polarity, value_json=value_json,
            source_id=source_id,
        ))
        conn.execute(
            """INSERT INTO memory_graph_claim_projection(claim_id,tenant_id)
               VALUES (%s::uuid,%s) ON CONFLICT(claim_id) DO NOTHING""",
            (claim_id, tenant_id),
        )

    def project_pending(self, *, tenant_id: str, limit: int = 100) -> ProjectionProgress:
        if not tenant_id or not (1 <= limit <= 1000):
            raise ValueError("Invalid tenant or batch limit")
        # SERIALIZABLE is not required for ledger uniqueness if writes are
        # protected by explicit per-edge row lock. Parallel projectors should
        # use SKIP LOCKED and re-read ledger after lock acquisition.
        projected = 0
        for _ in range(limit):
            with self.conn.transaction():
                edge = self.conn.execute("""
                    SELECT e.edge_id::text, e.from_entity, e.predicate, e.to_entity
                    FROM memory_edges e
                    WHERE e.tenant_id=%s AND e.retired_at IS NULL
                      AND (e.source_memory_id IS NULL
                           OR EXISTS (
                               SELECT 1 FROM memory_claims c
                               WHERE c.id=e.source_memory_id
                                 AND c.superseded_at IS NULL))
                      AND NOT EXISTS (SELECT 1 FROM memory_graph_projection p
                                      WHERE p.edge_id=e.edge_id)
                    ORDER BY e.recorded_at,e.edge_id
                    LIMIT 1 FOR UPDATE OF e SKIP LOCKED
                """, (tenant_id,)).fetchone()
                if edge is None:
                    break
                edge_id, src, relation, dst = edge
                # Lock acquired after selecting outstanding row. A different
                # transaction may have committed the projection before lock.
                already = self.conn.execute(
                    "SELECT 1 FROM memory_graph_projection WHERE edge_id=%s",
                    (edge_id,),
                ).fetchone()
                if already:
                    continue
                self._cypher(edge_upsert_cypher(
                    tenant_id=tenant_id, edge_id=edge_id, from_entity=src,
                    predicate=relation, to_entity=dst,
                ))
                self.conn.execute(
                    "INSERT INTO memory_graph_projection(edge_id) VALUES (%s)",
                    (edge_id,),
                )
                projected += 1
        return ProjectionProgress(attempted=limit, projected=projected)

    def reconcile_invalidated(self, *, tenant_id: str, limit: int = 100) -> int:
        """Remove projected edges that were retired or whose evidence was superseded.

        Same-transaction AGE DELETE and ledger DELETE permit replay after a crash.
        This does not garbage-collect orphaned vertices and does not support
        historical AGE snapshots.
        """
        if not tenant_id or not (1 <= limit <= 1000):
            raise ValueError("Invalid tenant or limit")
        removed=0
        for _ in range(limit):
            with self.conn.transaction():
                row=self.conn.execute("""
                    SELECT e.edge_id::text
                    FROM memory_edges e
                    JOIN memory_graph_projection p ON p.edge_id=e.edge_id
                    LEFT JOIN memory_claims c ON c.id=e.source_memory_id
                    WHERE e.tenant_id=%s
                      AND (e.retired_at IS NOT NULL
                           OR (e.source_memory_id IS NOT NULL
                               AND c.superseded_at IS NOT NULL))
                    ORDER BY e.recorded_at,e.edge_id
                    LIMIT 1 FOR UPDATE OF e SKIP LOCKED
                """,(tenant_id,)).fetchone()
                if row is None:
                    break
                edge_id=row[0]
                self._cypher(edge_delete_cypher(tenant_id=tenant_id, edge_id=edge_id))
                self.conn.execute(
                    "DELETE FROM memory_graph_projection WHERE edge_id=%s",(edge_id,))
                removed+=1
        return removed

    def neighbors(self, *, tenant_id: str, from_entity: str, hops: int = 1) -> list[str]:
        if not tenant_id or not from_entity:
            raise ValueError("Tenant and entity required")
        statement = graph_neighbors_cypher(
            tenant_id=tenant_id, from_entity=from_entity, hops=hops,
        )
        result = []
        for row in self._cypher(statement):
            # psycopg returns unregistered agtype as textual JSON for scalar
            # Cypher output; some drivers may use custom codecs.
            raw = row[0]
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(parsed, str):
                raise ValueError("Unexpected AGE entity ID type")
            result.append(parsed)
        return result
