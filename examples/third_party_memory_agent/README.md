# Independent third-party Memory Agent

This is a separate consumer project. It imports only the published
`agent_memory_core.sdk.MemoryClient`; it does not import server internals,
connect to PostgreSQL, or use Observatory/browser-specific endpoints.

## Install

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install 'agent-memory-core[api]>=0.1.0'
```

## Configure and run

Obtain a tenant-scoped API credential from an authenticated Owner/Admin
workspace session. The credential secret is returned once. Never put it in
source control or a browser bundle.

```bash
export MEMORY_BASE_URL='https://memory.example.com'
export MEMORY_TENANT_ID='your-workspace-id'
export MEMORY_API_KEY='amc_once_returned_secret'
python -m third_party_memory_agent
```

For a deployment with Temporal workers:

```bash
export MEMORY_RUN_TEMPORAL=1
python -m third_party_memory_agent
```

The executable consumer performs a real API lifecycle: readiness and identity,
create, revision, current/history reads, hybrid search, provenance retrieval,
event polling, optional Temporal verification, revoke, and history verification.
The process exits non-zero if any contract assertion fails.

## Trust boundary

All data operations use the public REST API through the SDK. The server decides
tenant scope from the authenticated credential and checks the supplied
`X-Memory-Tenant` against that scope. This project never receives a database
DSN, Keycloak admin credential, or server-side secret.
