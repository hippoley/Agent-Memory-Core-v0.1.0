# ADR-0005 — Composable public interface

Status: public contract + local REST prototype implemented.

Three separate planes:
- PostgreSQL/pgvector/AGE store claims, history and projections.
- Temporal executes long-lived tasks, optional and independent from normal reads.
- Objects store raw evidence by content hash.

The contract exposes *capabilities*, not implementation internals. The REST
gateway has four routes: GET /v1/health, GET /v1/capabilities,
POST /v1/memories and GET /v1/memories/{subject_id}.

Run local-only:
```bash
pip install -e '.[postgres,api]'
export MEMORY_DSN='postgresql://...'
export MEMORY_API_KEY='sufficiently-long-random-secret'
export MEMORY_TENANT_ID='home'
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
```

The API cannot import or require Temporal, so it keeps serving core SQL
memory when Temporal is not installed. Capability declarations conservatively
report graph/vector/Temporal as unavailable unless the application explicitly configures the corresponding adapter.

**Not suitable for public multi-tenant deployment.** Missing RLS, TLS,
rate-limits, identity lifecycle, object permissioning, deployment isolation,
full lifecycle tests and meaningful availability SLOs.

The hybrid search implementation catches optional vector/AGE failures and preserves canonical SQL search; canonical SQL failures propagate. A fake-adapter resilience test and real PostgreSQL triple-store test cover these paths.\n\nInvestment conformance benchmark must compare 5 variants on the same dataset:
SQL-only, SQL+vector, SQL+graph, all three and all three+Temporal.
Combined value must exceed integration and operations costs, and be supported
by external use and eventual actual paid pilots.
