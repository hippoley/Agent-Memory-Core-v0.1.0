# ADR-0003 — AGE is a transactional, rebuildable projection

Status: Implemented as prototype, awaiting real co-extension integration CI.

## Canonical model

`memory_claims` and `memory_edges` in PostgreSQL are authoritative.
The graph is a derived index. `memory_graph_projection` is a projection
ledger, with foreign keys to canonical edges.

## Projection contract

Each edge is locked and mapped into AGE graph via MERGE. The graph write and
projection ledger INSERT take place inside the **same local PostgreSQL
transaction**, eliminating a cross-database dual-write race. If any statement
fails, both graph and ledger roll back while canonical edge remains pending.

Node keys include tenant + entity identifier; relation type is fixed and
predicate stored as a property to avoid injecting user-controlled labels.
The API bounds traversal to one or two hops.

## Real-world limitations

- Concurrent projection is controlled with row locks and a per-edge ledger.
- AGE MERGE behavior, coexistence with pgvector and the installation image
  MUST pass the integration test, not just unit tests.
- Edits/deletions of canonical edges are not modeled yet; immutable edges
  must be superseded or handled by graph rebuild.
- No authenticated multitenant access or PostgreSQL row-level security yet.
- AGE graph query result serialization and performance require measurements.
- Composite hybrid ranking (SQL + vector + graph) remains an open gate.

## Grounding

Apache AGE documents Cypher MERGE and SQL transaction integration:
https://age.apache.org/age-manual/master/clauses/merge.html
https://age.apache.org/age-manual/master/intro/cypher.html
