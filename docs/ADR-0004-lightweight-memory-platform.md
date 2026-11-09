# ADR-0004 — Lightweight Autonomous Memory Infrastructure

Status: Proposed target architecture · 2026-11-09

## Decision in one sentence

One canonical memory core, one mandatory PostgreSQL process, optional in-process
workers, and adapters for object storage and durable workflow execution. Do not
turn every capability into a separately deployed service.

## 1. Keep six capabilities, not six infrastructure silos

| Concern | Technology | Authority / responsibility | Deployment |
|---|---|---|---|
| Facts, identity, provenance, bitemporal state, event/outbox | PostgreSQL | Sole canonical truth | Mandatory |
| Semantic retrieval | pgvector | Rebuildable vector index | Same PostgreSQL |
| Relationship traversal | Apache AGE | Rebuildable graph projection | Same PostgreSQL; optional feature |
| Raw evidence, audio, images, documents, large payloads | ObjectStore adapter | Immutable bytes addressed by SHA-256; SQL stores metadata/reference | Local filesystem by default; S3-compatible only when needed |
| Real-time updates | PostgreSQL transactions + outbox + NOTIFY wake-up | Outbox is durable; NOTIFY is only a wake-up hint | Same PostgreSQL; no Redis/Kafka initially |
| Long-running workflows | Temporal adapter | Durable orchestration, retries, timers, signals, recovery | Optional separate service, only when required |

Do not deploy Elasticsearch, Neo4j, Redis, Kafka, MinIO, and Temporal all at
once. Each adds operational cost and duplicates capabilities available in the
single-node profile.

## 2. Mem0: adopt the memory algorithm, not its platform dependency

Adopt these product/algorithm patterns behind our own interfaces:

1. Add: extract candidate durable facts from conversations, observations,
   decisions and documents; attach source/provenance and confidence.
2. Deduplicate: detect exact duplicates, semantic near-duplicates and entity
   aliases before committing new claims.
3. Entity extraction/linking: resolve people, projects, devices, rooms,
   places and concepts into canonical entity IDs.
4. Multi-signal retrieval: semantic similarity + lexical/BM25 + entity overlap
   + graph neighborhood + temporal relevance.
5. Explicit mutation: additions are additive by default; corrections and
   deletion/forgetting are explicit, auditable operations.
6. Retrieve before response: return a compact, provenance-carrying context
   packet, not an unbounded memory dump.

Important distinction: current Mem0 OSS documentation describes semantic,
keyword and entity matching, while its current Platform graph memory is a
managed capability. The OSS v2-to-v3 migration explicitly moved graph memory
to Platform. We must not assume that installing Mem0 OSS gives us that
Platform graph engine. We implement the useful algorithmic patterns natively
on PostgreSQL + pgvector + AGE. No Mem0 runtime dependency is required.

Mem0 is inspiration and a compatibility/evaluation reference, not the canonical
database, orchestration engine, or license to copy proprietary Platform code.

## 3. Canonical write path

```text
Input / sensor / tool / document
    -> source envelope + immutable raw object (if large)
    -> PostgreSQL inbox / idempotency key
    -> extraction candidate (local model or rules)
    -> dedup + entity resolution + policy validation
    -> transaction:
         append claim / correction / event
         append outbox event
         persist provenance + object hash
    -> asynchronous projections:
         embedding (pgvector)
         lexical index (Postgres tsvector / GIN)
         graph edges (AGE)
    -> acknowledge ingestion
```

The canonical claim commits first. Projection failures never lose the source
fact: outbox workers retry and can rebuild derived indexes. Never dual-write
PostgreSQL and AGE as separate databases.

## 4. Retrieval path

Run bounded retrieval channels in parallel:
- SQL exact filters: tenant, authorization, subject, source and valid_at/known_at.
- PostgreSQL full-text search (tsvector/GIN): names, IDs, exact terms.
- pgvector: semantic neighbors.
- AGE: bounded entity and relationship traversal.
- temporal signal: recency, validity and event-time constraints.

Fuse candidate rankings (e.g. reciprocal rank fusion), then re-check every
candidate against canonical PostgreSQL tenant, permission, provenance and
bitemporal predicates. Graph/vector scores never grant authority. Return
source IDs, timestamps, confidence and object references with each result.

Keep the fast path bounded: default 10-30 candidates/channel, AGE 1-2 hops,
strict query timeout, and no model call inside the database transaction.
Reranking by a local model is optional and only runs when the first pass is
ambiguous or the task is high value.

## 5. Real-time is not the same as workflow orchestration

A committed PostgreSQL transaction writes an outbox row. A worker consumes
the row and emits a subscription/update event. PostgreSQL NOTIFY can wake
the worker quickly, but it is not the durable queue: consumers always resume
from the outbox cursor and tolerate duplicate delivery.

For read-after-write, query the canonical claim immediately. Do not wait for
embedding or AGE projection. The response may indicate projection status;
semantic/graph recall catches up asynchronously. For device state, the latest
observation must carry source, observed_at, ingested_at, sequence/version and
quality. Memory does not itself assert that a physical command succeeded.

## 6. Temporal: optional control plane, not the memory database

Temporal is justified for workflows with durable waiting, long retries,
human approval, timers, compensation, or multi-step work that must survive
process restarts. Examples: document ingestion at scale, memory consolidation,
long-running sensor diagnosis, or device action plans with explicit evidence.

Do not start a Temporal workflow for every chat turn, SQL query, embedding or
simple memory insert. Those remain synchronous functions or a lightweight
in-process worker. A single outbox event may start a Temporal workflow when
the job crosses the durability/complexity threshold.

Workflow histories store orchestration state, not copies of all memory facts
or raw media. Activities call the Memory API using stable IDs and idempotency
keys. Temporal retries must not create duplicate claims. Signals/updates can
request cancellation or correction; canonical outcomes remain in PostgreSQL.

The core package must run and pass tests without Temporal installed. Add
`temporalio` only as an optional extra and keep the adapter in a separate
module/package. Temporal server is a separate deployment profile.

## 7. Object storage: start with the smallest correct implementation

Default single-node: local content-addressed filesystem, SHA-256, atomic write,
checksum on read, SQL metadata, and a reconciliation command for orphaned
objects/references. Do not put large blobs in PostgreSQL rows.

When multi-host access, lifecycle tiers, replication, or independent backup
is actually needed, switch the ObjectStore interface to an S3-compatible
backend. MinIO is optional, not mandatory. Store object hash, media type,
length, encryption key reference, retention class and source provenance in SQL.
Object deletion follows explicit retention/erasure policy, not graph cleanup.

## 8. Deployment profiles

**Profile S — laptop / home host (default):**
- One PostgreSQL instance with pgvector + AGE.
- One application process and one background worker process (can be same
  process in development).
- Local object directory.
- Local embedding/extraction model.
- No Temporal, Redis, Kafka, Elasticsearch, Neo4j or MinIO.

**Profile M — reliable multi-workflow node:**
- Same PostgreSQL and object store.
- Temporal server + workers only when durable workflow demand exists.
- Optional S3-compatible object store if workers span hosts.
- Outbox workers scale independently.

**Profile L — larger deployment:**
- Partition large observation/event tables by time only after measured volume.
- Tenant partitioning or dedicated indexes only after Recall@k and P95 show need.
- Keep AGE as derived graph; if graph becomes dominant, benchmark before
  considering an external graph service.

## 9. Lightweight is a measured property

Track:
- idle RAM and peak RAM per service;
- p50/p95 read and write latency;
- ingest-to-SQL visibility and ingest-to-vector/graph visibility;
- Recall@10 against exact baseline by tenant;
- outbox lag, retry count, dead-letter count;
- object bytes, orphan count, backup/restore duration.

Avoid premature microservices. The core must have a single-process test profile
and a single-PostgreSQL deployment profile.

## 10. Acceptance gates

A. Core works with PostgreSQL alone and no Temporal/Redis/Kafka/MinIO.
B. SQL + vector + FTS + AGE can be queried in one database with bounded time.
C. A crash after canonical commit but before projection is repaired by outbox.
D. Duplicate event delivery does not duplicate claims, edges or object bytes.
E. Corrections invalidate current graph/vector retrieval but preserve as-of
   historical SQL recall.
F. Read-after-write works before derived indexes catch up.
G. Object hashes are verified; missing/orphaned objects are detectable.
H. Temporal adapter can be disabled without changing memory semantics.
I. Mem0-inspired extraction/retrieval quality is benchmarked on our Chinese
   home-agent corpus, with explicit mutation and provenance tests.
J. Tenant isolation is enforced at the database boundary before production.

## Non-goals

- Reimplement Temporal.
- Depend on Mem0 Platform.
- Deploy six separate databases/services to claim feature completeness.
- Treat approximate similarity or graph reachability as truth.
- Put the model inside the transactional write path.


## 11. Temporal implementation status

Yes: Temporal is explicitly part of the target architecture as the open-source
Temporal Durable Execution platform (temporalio), not a generic timer or our
own imitation. Its role is an optional execution control plane. The current
repository contains the architecture boundary only; a working SDK adapter,
worker, workflow definitions, and self-hosted Temporal service profile are
not yet integrated. Do not describe Temporal as already running.

Official project: https://github.com/temporalio/temporal
Official docs: https://docs.temporal.io/temporal
