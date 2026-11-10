# S10 — Database-principal-bound tenant RLS (operator deployment)

This is an **opt-in, admin-installed** security boundary. It is intentionally
not included in `PostgresMemory.migrate()`: normal tenant connections must
never be able to create roles, grant privileges or install policies.

## Threat model and guarantee

Every tenant runs with its own PostgreSQL `LOGIN` role. A registry owned by
the database administrator maps that authenticated `session_user` to exactly
one tenant. A protected `SECURITY DEFINER` function reads the registry; RLS
policies compare the canonical tenant ID with that returned mapping.

No policy trusts `app.tenant_id`, `SET ROLE`, an API request field, or another
mutable custom session setting. Tenant logins must not own canonical tables,
have `BYPASSRLS`/`CREATEROLE`, or inherit worker/admin privileges.

This version protects PostgreSQL canonical claims, events, sources, objects,
entities, observations, idempotency keys, edges, embeddings, FTS, projection
ledgers and Webhook subscriptions/deliveries. Composite foreign keys prevent
tenant-ID-bearing links to claims or outbox events owned by other tenants.

**Not yet a full S10 release sign-off:** Apache AGE operates in a separate
graph schema and is deliberately **disabled in the HTTP gateway under RLS
mode** pending an independently reviewed graph permission boundary. The
independent worker/Temporal/MinIO roles also require separate credential
provisioning and fault/penetration acceptance.

## Admin installation, on an isolated database first

1. Back up the database. Verify prior canonical migrations are complete and
   test that legacy cross-tenant references do not violate the new foreign
   keys. The installer fails atomically if they do.
2. As a **database administrator**, install the RLS policies:

```bash
psql "$MEMORY_ADMIN_DSN" -v ON_ERROR_STOP=1 \
  -f sql/postgresql/security/001_principal_bound_rls.sql
```

3. As administrator, create a distinct login and bind its immutable mapping
   (use a secret manager; the example placeholders are not credentials):

```sql
CREATE ROLE memory_home_a LOGIN INHERIT PASSWORD '<generated-unique-password>';
GRANT memory_tenant TO memory_home_a;
INSERT INTO public.memory_rls_principals(role_name,tenant_id)
VALUES ('memory_home_a','home-a');
```

4. Start the tenant-specific API process using that login's restricted DSN:

```bash
export MEMORY_DSN='postgresql://memory_home_a:<password>@127.0.0.1:55432/memory'
export MEMORY_TENANT_ID='home-a'
export MEMORY_DB_RLS_MODE=1
export MEMORY_ENABLE_AGE=0
# MEMORY_API_KEY must also be configured
uvicorn agent_memory_core.server:app --host 127.0.0.1 --port 8765
```

The gateway checks the authenticated database principal **per connection**
before serving a canonical request or search. An administrator, incorrect
tenant mapping, or unprovisioned role fails closed.

**Never run** `PostgresMemory.migrate()` under the restricted tenant login;
the admin/operator must do that ahead of time. Never give `memory_tenant`
membership in an unrestricted worker or migration role.

## Acceptance evidence

`tests/test_s10_principal_rls.py` must run against a disposable real
PostgreSQL instance with a privileged `MEMORY_TEST_DSN`. It creates two
separate real LOGIN roles, verifies each sees only its own claims, embeddings,
objects and Webhook metadata, then attacks cross-tenant SELECT/INSERT/UPDATE/
DELETE, provenance links, spoofed `app.tenant_id` and unauthorized `SET ROLE`.
The legitimate tenant still writes successfully after the attacks. The test
removes ephemeral roles and connections.

Role grants and RLS need a real PostgreSQL server; mock database tests are
not sufficient. Production RLS hardening remains gated on the AGE/worker
security review and externally exercised tenant connections.
