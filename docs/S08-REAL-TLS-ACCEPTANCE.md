# S08 — independent HTTPS receiver acceptance

This is a real network / TLS integration test, not an HTTP mock. It runs on
the disposable GitHub Actions PostgreSQL runner and requires `openssl`.

`tests/test_s08_real_tls_receiver.py` provisions an ephemeral self-signed
X.509 server certificate with IP/DNS Subject Alternative Names, starts a
**different Python process** listening on loopback HTTPS, and sends a real
signed Webhook event through HTTPX/TLS. That process validates the timestamp,
HMAC-SHA256 signature, raw body and route, then records the received envelope
on disk before returning HTTP 204.

The test asserts:
- The Worker persisted and acknowledged exactly one delivery.
- The external process received the expected event ID and tenant.
- A tampered signature receives HTTP 401 and is not recorded.
- Re-polling does not redeliver an already acknowledged task.
- The destination allowlist can restrict an exact HTTPS host **and port**;
  allowing host:10443 does not authorize host:10444.

For production, use HTTPS port 443 and a publicly trusted CA unless a
specific internal CA is explicitly configured and maintained. The port
override is intended for explicitly allowlisted private integrations and
test deployments; never allow arbitrary hostnames or ports.

This closes the previously missing **separate-process TLS receiver test**,
not all production S08 concerns. Remote DNS rebinding resistance,
certificate rotation, external network outages, rate limits, receiver-side
durable idempotency, and genuine remote-environment onboarding need further
operational acceptance.
