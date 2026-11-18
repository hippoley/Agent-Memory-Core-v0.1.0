# Local Keycloak realm

This realm is for local development and CI only. It uses a deliberately
non-secret client credential and HTTP on loopback / `*.localhost`. Never reuse
these settings for a reachable network or production deployment.

## Start the complete local stack

From the repository root:

```bash
python scripts/launch_local.py up --profile lite --identity
```

This starts the existing PostgreSQL + API stack and adds Keycloak plus the
identity control-plane migration. The local identity profile deliberately uses
the isolated development database login for both migration and identity
requests; production must use the dedicated `memory_identity_service` LOGIN
documented in `docs/IDENTITY-CONTROL-PLANE-DEPLOYMENT.md`.

- Observatory: http://127.0.0.1:8765/dashboard/observatory.html
- OIDC login: http://127.0.0.1:8765/v1/auth/oidc/login
- Keycloak admin: http://127.0.0.1:8081
- Realm: `agent-memory`
- Client: `agent-memory-local`
- Callback: `http://127.0.0.1:8765/v1/auth/oidc/callback`
- PKCE: S256
- Local admin: `admin` / `local-keycloak-admin-change-me`
- Local client secret: `local-only-change-me`

The realm allows self-registration for the local onboarding test. First login
creates the Agent Memory Principal, first Workspace, Owner Membership and
opaque Session. Use a throwaway local account; do not place real user data in
this development realm.

The local profile sets `MEMORY_OIDC_ALLOW_INSECURE_HTTP=1`. Application code
still permits HTTP only for loopback and `*.localhost` hosts. Production must
leave this flag disabled, use HTTPS for the issuer, redirect URI and all
discovery endpoints, and provide unique secrets through a secret manager.

Stop the stack with:

```bash
python scripts/launch_local.py down --identity
```

The command removes containers but preserves local volumes. To remove the
Keycloak and database development data too, run the corresponding Compose
`down -v` command deliberately.
