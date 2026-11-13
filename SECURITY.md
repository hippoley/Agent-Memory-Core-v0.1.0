# Security policy

Agent Memory Core v0.1.0 is an **experimental local-first research prototype**. It is not qualified as an Internet-facing, multi-customer memory SaaS.

## Supported version

Security fixes are considered against the latest development branch and `main`. No production support or vulnerability response-time SLA is currently offered.

## Private disclosure

For vulnerabilities, use GitHub's **Report a vulnerability** / private security advisory feature under this repository's Security tab **if enabled**. If it is unavailable, open a public issue **without exploit code, secrets or sensitive details**, asking maintainers to establish a private disclosure channel. Do not send API keys or user data in public issues.

## Safe use

- Bind the demo API to loopback. Use a reverse proxy, TLS, authentication and isolation controls before any non-local deployment.
- Never share local `MEMORY_API_KEY`, PostgreSQL credentials, signing keys or tenant memory records.
- Treat the existing Compose database password as **development-only**.
- Do not treat a single tenant-bound API key as a complete production identity service. Verify DB-level RLS and object store ACLs for hostile tenants.
- Configure webhook destination allowlists and signing keys and require receiver-side idempotency.
- Avoid putting real private data into publicly hosted interactive demos, GitHub Actions logs, issue reproductions or example scripts.

The release [acceptance matrix](docs/USER-STORY-ACCEPTANCE.md) lists the remaining security gates. A passing CI test does not establish production hardening.
