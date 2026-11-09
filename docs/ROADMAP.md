# Roadmap · Implementation and verification gates

The repository is a **working local research prototype**, NOT a secure production service.

| Gate | Work | Current evidence | Status |
| --- | --- | --- | --- |
| G0 | SQLite fact/time/object core | Unit tests in `test.yml` | ✅ Prototype verified |
| G1 | PostgreSQL fact tables + pgvector search | Real Postgres + vector write/query in `integration.yml` | ✅ Prototype verified |
| G2 | AGE two-hop traversal, canonical edge projection | Real same-database graph test, idempotent replay | ✅ Prototype verified |
| G3 | SQL + vector + graph hybrid recall | Real three-channel candidate merge and tenant test | ✅ Prototype verified |
| G4 | Bitemporal interval correction, historical evidence semantics | Basic validity/as-of only, full interval revision missing | ◻ Incomplete |
| G5 | Authentication, tenant RLS, encryption, erasure and recovery | Threat model and adversarial database tests absent | ◻ Not started |
| G6 | Graph deletion/reconciliation and rebuilding | Immutable-edge projection only | ◻ Incomplete |
| G7 | Local embeddings, retrieval quality and latency benchmarks | Fixed 384-dim profile, no local encoder or Recall@k | ◻ Incomplete |
| G8 | Real sensor time-series partitioning and object lifecycle | Observation schema and SHA-256 store only | ◻ Incomplete |
| G9 | Temporal / Restate / WindowPilot adapters | Boundary documented; no runtime adapters | ◻ Not started |
| G10 | Interop spec + external reproducer | Independent external verification absent | ◻ Not started |

## Verified version profile

GitHub Actions real database verified PostgreSQL **18.6**, Apache AGE **1.8.0**,
pgvector **0.8.7**, on 2026-11-09. This is the observed integration profile;
the Dockerfile is based on `apache/age:latest`, so image revisions can change.
For reproducibility, pin base digest and package versions before production.

## Next blocking work

1. Complete graph rebuild and deletion propagation before mutable edge governance.
2. Enforce tenant authorization at the database boundary, not just query wrappers.
3. Implement local embeddings and relevance/latency evaluation on real Chinese agent corpora.
4. Define physical evidence and workflows only through adapters, not memory side effects.

Not planned: replicating Temporal, hosting a multi-model cloud platform or
implementing a new database engine.
