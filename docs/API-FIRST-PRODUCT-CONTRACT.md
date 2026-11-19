# API-first Product Contract

**Status:** Normative product baseline  
**Applies to:** REST API, OpenAPI, Python SDK, Observatory, workers and all future clients  
**Release gate:** Third-party independent acceptance

## 1. Non-negotiable principle

Agent-Memory-Core is an API-first product. The REST API is the product boundary.
Observatory, the Python SDK, MCP adapters, external agents and future clients are
consumers of the same public contract.

A feature is not complete merely because a button works, a page renders, an
internal Python method exists, a worker unit test passes, or a database row can
be inserted manually. Every user-visible product capability must be reachable
through an authenticated public API, documented in OpenAPI, and callable by a
third-party client without importing server internals or connecting directly to
the database.

No UI-only business logic. No frontend-only authorization. No test-only bypass.
No undocumented private endpoint as the only way to complete a User Story.

## 2. Contract rules

1. **REST is canonical.** All product reads and mutations have stable REST
   operations. Browser clients may add presentation state, never duplicate
   business rules.
2. **OpenAPI is authoritative.** Every public operation has request/response
   schemas, authentication requirements, status/error semantics, capability
   requirements, and an executable example where appropriate.
3. **SDK is a thin public client.** SDK methods map to documented REST
   operations. The SDK must not import server modules, access database drivers
   for product operations, or silently emulate unavailable server capabilities.
4. **Identity is server-enforced.** Every identity-mode request carries a
   validated principal, active membership, tenant and role. API credentials
   are scoped, expiring, revocable and tenant-bound. A caller-supplied tenant ID
   is a selector, never proof of authorization.
5. **Capabilities are explicit.** Optional channels such as vector search,
   graph, object storage and Temporal must be discoverable through
   `GET /v1/capabilities`. A disabled capability returns a clear documented
   error; the SDK must not pretend it succeeded.
6. **Writes are recoverable.** Mutations support idempotency where retry can
   duplicate effects. Timeouts are ambiguous, not evidence of rollback. Clients
   can read back canonical state and durable events.
7. **Asynchronous work is API-visible.** Starting, inspecting, cancelling,
   replaying and observing asynchronous work are public operations. Every
   side-effecting worker boundary rechecks live authorization and tenant
   scope.
8. **Evidence and history remain addressable.** Current state, superseded
   versions, provenance and object references are available through APIs under
   the same tenant boundary.
9. **Compatibility is deliberate.** Breaking changes require an explicit
   contract version/migration plan. Legacy single-tenant Bearer mode is a
   compatibility mode, not the multi-user identity design.
10. **The UI is not a privileged path.** Observatory must use the same REST
    operations and permission checks available to external clients.

## 3. User Story API coverage

| User Story | Public API surface | Acceptance proof |
|---|---|---|
| US-01 Identity and first use | OIDC login/callback, identity/me, tenants, select-tenant, credential issue/revoke | Real IdP, two users, separate workspaces, no cross-tenant access |
| US-02 Create and process memory | POST memories, POST memories/process, GET memories | External SDK creates and reads canonical PostgreSQL state |
| US-03 Hybrid retrieval | POST search, GET capabilities | External SDK query returns current valid claim and provenance |
| US-04 Revision and history | POST memories with replaces_id, GET memories with include_superseded, GET sources | External SDK revises and independently reads current + historical versions |
| US-05 Revoke/forget | DELETE memories/{id}, DELETE memories | External SDK verifies active retrieval excludes forgotten claims while history remains |
| US-06 Durable async work | POST/GET/POST-cancel workflows, GET events, SSE, outbox replay, webhook APIs | Real Temporal worker, persisted state, event delivery and observable completion |
| US-07 Authorization changes | identity/session/credential lifecycle + worker authorization | Revoke session, credential or membership after enqueue; queued work is denied before side effects |
| US-08 Failure and recovery | event replay, webhook replay, Temporal retry/status, canonical read APIs | Kill worker / interrupt database or callback, restart, replay idempotently and read final state |

The table describes required product coverage, not a claim that every row has
already passed production acceptance. Optional endpoints are usable only when
their advertised capability is enabled.

## 4. Third-party independent acceptance

The release gate is a separate consumer project with these constraints:

- Its source tree imports only the published `agent_memory_core.sdk` API.
- It does not import `agent_memory_core.api`, `identity`, `postgres`,
  worker modules, or any other server implementation module.
- It has no database DSN, migration file, Keycloak admin credential, or direct
  database/network access except the public HTTP service.
- It installs the built distribution wheel into a fresh virtual environment.
- It receives a short-lived tenant-scoped API credential through the supported
  Owner/Admin identity flow.
- It uses the public SDK to create, revise, search, read history, retrieve
  provenance, read events, optionally use object storage and Temporal, forget,
  and verify the retained history.
- A negative test proves a credential cannot select another tenant and that a
  revoked credential is rejected.
- The run emits a machine-readable result and fails non-zero on any contract
  violation.

The executable consumer is maintained at
[`examples/third_party_memory_agent`](../examples/third_party_memory_agent/README.md).
The Product E2E workflow builds the wheel, provisions a real credential from the
Keycloak browser session, installs the wheel into a clean venv and runs the
consumer against the running service.

## 5. Definition of Done for every product capability

A capability may be marked **complete** only when all applicable checks pass:

1. Public REST route exists and is authenticated.
2. OpenAPI schema and operation documentation are correct.
3. Python SDK can call it without private imports.
4. Observatory uses that same REST contract where a UI exists.
5. PostgreSQL/object/event/Temporal side effects are persisted correctly.
6. A separate read request can independently verify the result.
7. Tenant, role, credential scope and revocation boundaries are tested.
8. Retry, idempotency and failure behavior are tested where applicable.
9. The clean third-party consumer can complete the relevant User Story.
10. Documentation and examples match the shipped behavior.

A green unit-test job alone does not satisfy this definition.

## 6. Current implementation boundary

PR #51 adds identity-scoped API credential issuance/revocation, tenant-aware
SDK calls, history and workflow methods, and a standalone third-party consumer
project. The Product E2E workflow is being extended to install the wheel in a
fresh environment and run that consumer against the real Keycloak/PostgreSQL/
Temporal stack.

This is an acceptance implementation in progress. Do not label the product
Public Release, production SaaS, or fully multi-tenant until the latest-head
third-party workflow, membership administration, full worker recovery,
webhook delivery/replay, clean deployment and restore tests have all passed.
