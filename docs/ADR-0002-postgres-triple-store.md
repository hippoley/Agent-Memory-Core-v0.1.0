# ADR-0002 · PostgreSQL + pgvector + Apache AGE

Status: **Accepted as target architecture; PostgreSQL, pgvector and AGE prototype implemented and co-extension CI tested**.

## Decision

PostgreSQL is the transactional source of truth. Facts, identity, provenance,
bitemporal validity, events, time-series records and object references live
in PostgreSQL. pgvector is the semantic retrieval index. Apache AGE is an
optional graph projection for multi-hop relationships.

The three capabilities use one PostgreSQL server/database only when AGE and
pgvector are actually installed in the same compatible build. Supporting
both individually does not prove the combined runtime.

The SQL adjacency table is canonical; the separate Apache AGE graph projection and bounded two-hop Cypher traversal are implemented and CI tested. Full rebuild and projection deletion/reconciliation are still missing.

## Evidence semantics

Each claim includes tenant, subject, predicate, value, source, validity interval,
recording time and replacement link. Queries distinguish valid_at from known_at.
An object SHA256 points to immutable local bytes; object storage itself remains
outside the SQL transaction. Late observations preserve observed_at vs ingested_at.

## Phased gates

1. PostgreSQL data contract and smoke tests.
2. Local embedding backend (model-identified dimension 384) and tenant-safe recall benchmarking.
3. AGE projection from canonical `memory_edges` with restart-safe reconciliation.
4. Hybrid SQL + vector + AGE Cypher read path with provenance filtering and quality tests.
5. Optional time partitioning, object lifecycle, auth/RLS and deletion semantics.

## Rejected approaches

- AGE as an independent source of mutable facts.
- Unconditional cloud LLM API requirement.
- Claiming SQLite prototype has graph or vector retrieval.
- Treating time-series as a graph substitute.

## Constraints

The AGE docs and main branch can disagree on supported PostgreSQL versions.
Pin an explicit PostgreSQL/AGE/pgvector build combination and verify all three
extensions on a real PostgreSQL instance before considering the combination supported.

No embedding inference or production PostgreSQL instance has been tested
by this repository yet. Server-side vector indexes under tenant filters require
Recall@k comparisons to exact search.

## As-of semantic retrieval

Vector candidates are filtered by tenant, `known_at` (transaction time),
and `valid_at` (real-world validity) *before* cosine ranking. A correction
records both the prior supersession and replacement insertion at a shared
database time. This closes the intermediate historical gap between the two
versions. Full effective-date interval splitting remains a separate gate.

The index is exact by default to avoid filtered HNSW neighbor starvation;
tenant-partitioned ANN requires Recall@k/P95 evidence before activation.

## Embedding profile compatibility

The legacy SQL table has `vector(384)`. The local Chinese candidate
`BAAI/bge-small-zh-v1.5` outputs 512 dimensions. Both profiles now have
separate columns/indexes and a per-tenant model-dimension registry. A single
model ID cannot silently switch dimensions. Tests use fixture vectors, not
actual model inference or retrieval-quality benchmarks.

See https://huggingface.co/BAAI/bge-small-zh-v1.5 for 512-dimension model.
