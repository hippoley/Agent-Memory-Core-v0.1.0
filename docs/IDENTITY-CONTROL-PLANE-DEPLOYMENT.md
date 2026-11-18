# Identity control-plane deployment

This is a separate control-plane connection. Do not reuse the restricted tenant
PostgreSQL login and do not expose this DSN to browsers or tenant SDKs.

## Install

1. Apply the ordered application migrations through `005_identity_foundation.sql`
   using the database migration administrator.
2. As a database administrator, run:

```bash
psql "$MEMORY_ADMIN_DSN" -v ON_ERROR_STOP=1 \
  -f sql/postgresql/security/002_identity_service_role.sql
```

3. Create a dedicated LOGIN role and grant it membership in
   `memory_identity_service`. Use a unique generated secret and a secret manager.
   Grant CONNECT on the application database and USAGE on schema public.
4. Configure the API process with `MEMORY_IDENTITY_DSN` pointing to this
   dedicated login. Keep `MEMORY_DSN` for the memory data plane.

## Current HTTP behavior

When `MEMORY_IDENTITY_DSN` is configured, the gateway accepts:
- Opaque session tokens in the `amc_session` HttpOnly cookie or Bearer header,
  with `X-Memory-Tenant` selecting a tenant the principal currently belongs to.
- Scoped API credentials in the Bearer header, also paired with
  `X-Memory-Tenant`.
- Legacy single-tenant `MEMORY_API_KEY` remains supported for migration.

Every request resolves the current session/credential and active membership from
PostgreSQL. Membership downgrade/revocation therefore takes effect on the next
request. Reader memberships are read-only; API credential scopes are enforced.
Cookie-authenticated mutations require a matching `amc_csrf` cookie and
`X-CSRF-Token` header.

`GET /v1/identity/me` returns the authenticated principal context.
`POST /v1/auth/logout` revokes the current session and clears session cookies.

## Not yet an open registration deployment

This slice does not implement OIDC authorization-code + PKCE, provider discovery,
JWKS signature verification, login/callback routes, initial-owner bootstrap,
tenant administration UI, or browser cookie issuance. Do not expose public
registration until those pieces and the browser end-to-end flow are complete.
