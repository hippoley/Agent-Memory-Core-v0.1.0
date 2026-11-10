# PostgreSQL prototype

Prerequisites: a PostgreSQL build with pgvector installed and a separate
AGE-compatible build. Use a locally configured instance, not a cloud API.

1. Create a database and role with minimal privileges.
2. Apply `001_core.sql` using psql with `ON_ERROR_STOP=1`.
3. Apply `003_outbox_recovery.sql` to add bounded retry/dead-letter state.
4. Verify pgvector and SQL inserts and queries.
5. **Only after installing AGE in the same server**, apply `002_age.sql`.
5. Run cross-extension integration, tenant isolation and graph projection tests.

Example (credentials omitted):

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/postgresql/001_core.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/postgresql/003_outbox_recovery.sql\npsql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/postgresql/002_age.sql
```

The AGE initialization is not idempotent where `create_graph` already exists;
run only on fresh database or handle graph creation explicitly.

**Security:** DDL is setup-only. The current service still relies on application-side tenant filters; database-enforced RLS, key lifecycle, and adversarial multitenant verification remain release blockers. Do not expose directly to untrusted tenants.

**Packaging:** `PostgresMemory.migrate()` applies ordered, packaged migrations from `agent_memory_core/resources/`; wheels include these SQL resources.
