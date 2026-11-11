# PostgreSQL schema and migration guide

Prerequisites: PostgreSQL with pgvector installed. Apache AGE is initialized
separately because it is an optional derived graph projection.

## Ordered canonical migrations

`PostgresMemory.migrate()` applies the packaged SQL resources in numeric order:

1. `001_core.sql` — canonical claims, provenance, object metadata, events,
   outbox, embeddings, entities, graph edges and search projections.
2. `002_webhooks.sql` — durable Webhook subscriptions and deliveries.
3. `003_outbox_recovery.sql` — bounded retry and dead-letter state.
4. `004_predicate_cardinality.sql` — trusted tenant-scoped `one` / `many`
   predicate policy.

Source files under `sql/postgresql/` are byte-for-byte mirrored into
`src/agent_memory_core/resources/`; package tests enforce this.

Example for a disposable local database:

```bash
psql "$MEMORY_ADMIN_DSN" -v ON_ERROR_STOP=1 -f sql/postgresql/001_core.sql
psql "$MEMORY_ADMIN_DSN" -v ON_ERROR_STOP=1 -f sql/postgresql/002_webhooks.sql
psql "$MEMORY_ADMIN_DSN" -v ON_ERROR_STOP=1 -f sql/postgresql/003_outbox_recovery.sql
psql "$MEMORY_ADMIN_DSN" -v ON_ERROR_STOP=1 -f sql/postgresql/004_predicate_cardinality.sql
```

AGE setup is separate and must be run only after installing AGE in the same
PostgreSQL server. Review `002_age.sql` before applying it; graph creation is
not a substitute for canonical SQL migrations.

## Principal-bound RLS (opt-in operator installation)

`security/001_principal_bound_rls.sql` is deliberately **not** an automatic
numbered migration. It requires administrator privileges and distinct
PostgreSQL LOGIN principals per tenant. Its trusted principal mapping is based
on authenticated `session_user`, not a mutable custom GUC.

Follow `docs/S10-PRINCIPAL-RLS.md`. Run it only on a backed-up disposable
environment first. Tenant API roles can read their own predicate-cardinality
policy but cannot change it. Do not run schema migrations under tenant API
credentials.

## Operational boundary

PostgreSQL is the canonical source of truth. Vector, FTS and AGE are derived
projections. Delivery is at-least-once; consumers must be idempotent. Do not
claim production readiness from unit tests alone: run the real PostgreSQL,
AGE, pgvector, Temporal, RLS and failure-recovery acceptance on one immutable
release candidate SHA.
