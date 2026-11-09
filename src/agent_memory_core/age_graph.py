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

    def _cypher(self, statement: str) -> list[tuple]:
        """AGE requires Cypher query text as a SQL literal, not bind placeholder."""
        query = sql.SQL(
            "SELECT * FROM ag_catalog.cypher({}, {}) AS (result ag_catalog.agtype)"
        ).format(sql.Literal(GRAPH_NAME), sql.Literal(statement))
        return list(self.conn.execute(query))

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
                    WHERE e.tenant_id=%s
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
