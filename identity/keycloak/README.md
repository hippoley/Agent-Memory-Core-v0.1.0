# Local Keycloak realm

This realm is for local development and CI only. It uses a deliberately
non-secret client credential and HTTP on loopback. Never reuse these settings
for a reachable network or production deployment.

- Realm: `agent-memory`
- Client: `agent-memory-local`
- Callback: `http://127.0.0.1:8765/v1/auth/oidc/callback`
- PKCE: S256 required by the client configuration
- Self-registration: enabled for the local realm
- Keycloak admin: `admin` / `local-keycloak-admin-change-me` (override in Compose)

Start with `docker compose -f compose.keycloak.yaml up -d --wait`.
Open `http://127.0.0.1:8081` to administer the local realm.
