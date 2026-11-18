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

## Current release boundary

OIDC Authorization Code + PKCE, provider discovery, JWKS verification,
login/callback routes, first-Owner workspace bootstrap and browser Session
cookies are implemented on PR #51. The Keycloak local profile and a real
Chromium acceptance workflow are included.

This is not yet approval for public registration or production SaaS. Real
external-provider deployment, workspace administration, invitations, full
resource-level authorization, production data-plane isolation, rate limiting,
secret rotation, provider logout and independent security review remain
separate release gates.

## OIDC browser login (first-provider implementation)

Install the optional verifier dependencies:

```bash
pip install -e '.[oidc]'
```

Configure one trusted OIDC issuer and register the exact callback URI with that
provider:

```bash
export MEMORY_OIDC_ISSUER="https://id.example.com"
export MEMORY_OIDC_CLIENT_ID="..."
export MEMORY_OIDC_CLIENT_SECRET="..."
export MEMORY_OIDC_REDIRECT_URI="https://memory.example.com/v1/auth/oidc/callback"
export MEMORY_OIDC_STATE_SECRET="$(openssl rand -base64 48)"
export MEMORY_OIDC_ALLOW_SIGNUP="0"
export MEMORY_COOKIE_SECURE="1"
```

Set `MEMORY_OIDC_ALLOW_SIGNUP=1` only when public account creation is intended.
With it disabled, only an already-linked issuer + subject can sign in. The
callback never links accounts by email. A verified OIDC identity is atomically
bound to a Principal; if it has no active Membership and workspace bootstrap is
enabled by the application, a first workspace and Owner Membership are created
in the same transaction as the 12-hour opaque Session.

Browser entry point: `GET /v1/auth/oidc/login`. The callback validates the
signed, ten-minute transaction cookie, State, PKCE S256 verifier, ID-token
signature via provider JWKS, exact issuer, audience, expiry and nonce. Session
tokens are HttpOnly cookies; the CSRF token and selected tenant are separate
same-origin cookies. The Observatory sends the selected tenant and CSRF header
for cookie-authenticated API calls.

The provider issuer and all discovered authorization, token and JWKS endpoints
must use HTTPS. Keep the client secret and state-signing secret server-side.
This is a single-provider browser-login slice; provider logout, account linking,
MFA policy, invitation flow, and external-browser production acceptance remain
separate delivery work. Do not treat passing unit tests as production deployment
approval.


## Local Keycloak integration

For a reproducible local provider, run:

```bash
python scripts/launch_local.py up --profile lite --identity
```

The identity overlay starts Keycloak at `http://127.0.0.1:8081`, configures
the `agent-memory` realm and `agent-memory-local` client, applies the identity
schema to the isolated local database, and wires the API to the control plane.
Open the Observatory at `http://127.0.0.1:8765/dashboard/observatory.html`
and select **Sign in with organization**.

The local-only profile sets `MEMORY_OIDC_ALLOW_INSECURE_HTTP=1`. HTTP is
accepted only for loopback and `*.localhost` hosts. The local profile shares
the development database login for migration and identity access; it is not
the production privilege model. Never expose this profile to a network.

The real-provider CI acceptance is
`tests/test_oidc_keycloak_browser.py`, run by
`.github/workflows/keycloak-oidc-e2e.yml`. It creates an IdP user, completes
the actual Keycloak login in Chromium, verifies first-Owner provisioning,
creates and withdraws a memory through the real API, checks session persistence
after reload, and verifies logout revokes the Session.
