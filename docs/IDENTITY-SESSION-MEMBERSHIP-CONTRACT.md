# Identity, Session and Membership Contract

Status: design contract for the next implementation slice. This document does **not** claim OIDC, browser sessions or multi-user authorization are implemented.

## Why this is separate from S10

The current API authenticates one configured bearer secret and binds every request in that process to one configured `MEMORY_TENANT_ID`. In opt-in database-principal RLS mode, PostgreSQL additionally verifies that the process connects as a restricted LOGIN mapped to that tenant through `session_user`.

That is a useful tenant data boundary, but it is not an end-user identity system:

- no OIDC issuer/client registration, authorization-code + PKCE flow or JWT/JWKS validation;
- no persisted browser session, refresh/rotation or immediate session revocation;
- no user/principal directory or tenant membership lifecycle;
- no role/permission checks distinguishing owner, admin, editor, reader or service principal;
- no credential inventory, per-token scopes, rotation or revocation for API clients.

Do not rename the existing API key as a user session or infer a user from a tenant ID.

## Security principals

Use distinct concepts and identifiers:

1. **External identity**: immutable pair `(issuer, subject)` from a verified OIDC identity token. Email and display name are mutable profile attributes, never authorization keys.
2. **Local principal**: stable internal UUID linked to exactly one external identity pair. A principal may be a human or a service identity; types are explicit.
3. **Tenant**: the resource and billing/security boundary. A principal has no tenant access without an active membership or explicit service grant.
4. **Membership**: `(tenant_id, principal_id, role, status, created_at, revoked_at, version)`. Role changes and revocation take effect on the next authorization decision; do not trust role claims cached in a long-lived browser token.
5. **Session**: opaque random identifier; persist only a cryptographic hash, principal, issued/last-seen/expiry times, revocation time, authentication time and session version. Rotate on login and privilege change.
6. **API credential**: separate machine credential with hashed secret, owner principal, tenant, scopes, expiry, last-used and revoked-at fields. Never store or return the raw secret after creation.

## Request authorization pipeline

For every protected request:

1. Validate the OIDC callback using state, nonce, PKCE and exact redirect URI; validate issuer, audience, signature, expiry and allowed algorithms against trusted issuer metadata/JWKS.
2. Resolve `(issuer, subject)` to a local principal. Do not auto-provision tenant membership from an untrusted claim.
3. Resolve the opaque session hash and reject missing, expired, revoked or version-mismatched sessions.
4. Load the active membership for the requested tenant and evaluate the required permission for the resource/action. The tenant selector is only a lookup hint; it is never proof of membership.
5. Derive the tenant ID and principal ID from the validated server-side context. Ignore or reject caller-supplied tenant/principal fields for authorization.
6. Open a database connection under the restricted tenant database LOGIN configured for that tenant. Retain S10's `session_user`-bound RLS as the independent tenant fence. Never use a privileged migration/worker login for request traffic.
7. Emit an audit event with correlation ID, principal, tenant, action, resource, decision and reason code. Do not log bearer secrets, cookies, raw identity tokens or memory contents.

A membership revocation or role downgrade must be visible on the next request, including when the browser session itself remains valid. Any cache must have explicit bounded staleness and a revocation invalidation mechanism; initial release should prefer authoritative reads.

## Authorization matrix (initial product roles)

| Action | Owner | Admin | Editor | Reader | Service |
| --- | --- | --- | --- | --- | --- |
| Read/search memories and history | yes | yes | yes | yes | scope-gated |
| Create/revise/retract memories | yes | yes | yes | no | scope-gated |
| Manage members and roles | yes | yes | no | no | no |
| Create/revoke API credentials | yes | yes | no | no | no |
| Change tenant security settings | yes | yes | no | no | no |
| Delete tenant / export all tenant data | yes | policy-gated | no | no | no |

The first implementation must encode permissions as named capabilities, not scattered string comparisons. Resource-level ownership or project sharing is a later layer; tenant membership must not imply permission to administer another principal's private resources.

## Database boundary and operational model

- S10 remains opt-in and operator-installed. Its role registry maps one restricted PostgreSQL LOGIN to one tenant. It is a tenant isolation boundary, not per-user row ownership.
- The API performs principal/membership authorization before using the tenant-scoped DB connection. A tenant's DB LOGIN must not be exposed to browser clients.
- No request path may call `PostgresMemory.migrate()`, install RLS, create roles, or access the admin DSN.
- AGE remains disabled in restricted RLS mode until graph schema permissions and tenant-safe traversal receive independent acceptance.
- Worker, Temporal, object-store and migration identities are separate credentials with least privilege; they are not interchangeable with browser sessions.

## Migration and compatibility

1. Keep existing single-tenant bearer-key mode working for local development and existing integrations.
2. Add the identity/session/membership schema and repository operations in a separately versioned migration; do not silently convert existing API keys into human users.
3. Introduce an injected request-authentication context and permission dependency while preserving the current tenant-bound API-key adapter.
4. Add OIDC as an optional provider adapter. Keep provider-specific discovery/JWKS logic behind a maintained OIDC library; do not implement JWT cryptography manually.
5. Add a provisioning command that explicitly creates the first tenant owner and maps the tenant to its restricted DB LOGIN. No implicit first-user-is-admin behavior.
6. Only enable multi-user mode after same-head PostgreSQL, HTTP and browser acceptance passes. Document the downgrade/rollback path before deployment.

## Required adversarial acceptance

- Invalid issuer, audience, signature, algorithm, nonce, state, PKCE verifier, expired identity token and wrong redirect URI are rejected.
- Session cookie is HttpOnly, Secure in deployment, SameSite appropriate, rotated after login and privilege change; CSRF is rejected on state-changing browser requests.
- Expired, revoked and version-stale sessions fail on the very next request.
- Removing membership or downgrading Editor to Reader immediately blocks writes without requiring a new login.
- A valid principal with no membership cannot access a tenant by guessing its ID.
- A reader cannot create/revise/retract; an editor cannot manage membership or credentials.
- Revoked API credentials fail immediately; rotated credentials do not revive old secrets; scopes are enforced.
- Two principals in one tenant obey distinct role permissions; two tenants remain isolated through S10 RLS even if application filters are accidentally omitted.
- A privileged/admin DB login, unregistered LOGIN, role with BYPASSRLS, table owner and wrong tenant mapping all fail closed for request traffic.
- Direct SQL attempts to change tenant GUCs, use SET ROLE, forge foreign references, read registry tables or access AGE must not cross the tenant boundary.
- Audit events correlate login, session, membership change, credential lifecycle and memory mutation without recording secrets.

## Definition of done

A clean deployment can register/login a first owner through OIDC, create a second member, change roles, revoke membership and sessions, issue/revoke a scoped service credential, and complete the Observatory create/search/revise/retract/history journey. Every action is checked against current membership, and independent PostgreSQL RLS tests prove tenant isolation. A third party can reproduce this from published docs without a maintainer manually editing database rows.
