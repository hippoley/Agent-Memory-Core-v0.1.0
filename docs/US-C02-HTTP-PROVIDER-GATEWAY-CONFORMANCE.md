# US-C02 · Real HTTP authorization boundary (reference conformance)

`HTTPDurableEffectProvider` is a reusable stdlib HTTP adapter implementing the
existing `DurableEffectProvider` protocol. Unlike the earlier in-process
`FakeProvider`, it sends **actual HTTP requests** to a separate HTTP resource
server. It requires **two different bearer credentials**:
- ACTUATE: `POST {base_url}/effects`, body `{request,idempotency_key}`
- LOOKUP: `GET {base_url}/effects/{idempotency_key}`, returning an explicit
  `{outcome:"committed"|"not_found"|"unknown"|"failed"}`.

HTTP 401/403 from ACTUATE are definitive `ProviderRejected` only when the
gateway guarantees auth is checked before effects. Other errors are ambiguous.
HTTP 404 from LOOKUP is **not** treated as authoritative NOT_FOUND. Redirects
are rejected to avoid forwarding bearer tokens to another host. TLS is required
except loopback HTTP for local contract tests.

Sample usage (with independently issued, limited-scope gateway credentials):
```python
from agent_memory_core.http_effect_provider import HTTPDurableEffectProvider
provider = HTTPDurableEffectProvider(
    base_url="https://gateway.example/api/v1",
    actuation_token=actuate_secret,
    lookup_token=readonly_lookup_secret,
)
# Use this object only through Worker's ctx.execute_effect and
# ctx.reconcile_effect; never store tokens in task input or checkpoints.
```

`tests/test_http_effect_provider_gateway.py` starts an **actual loopback HTTP
server** with distinct write/read credentials, revokes write access, checks that
a new operation fails at the RESOURCE SERVER while a previous committed
operation can still be read by the lookup-only token, and verifies stable
idempotency prevents duplicate remote effects. It is not a real device or
production gateway. The server is test-only and should never be deployed.

## Remaining production requirements
1. An independently managed real gateway must issue/revoke constrained
   credentials and enforce tenant, principal, device, action and expiry.
2. A gateway must prove stable idempotency and authoritative lookup retention;
   this adapter's capabilities reflect a **contract assumption**.
3. Network errors can occur after remote commit; AMC keeps these as UNKNOWN.
4. Physical readback and independent provenance remain unverified. An HTTP
   receipt alone is **not** proof the device changed.
5. Do not merge this PR as a completed US-C02 until an actual gateway is tested.

This artifact provides a **portable gateway-facing contract**, not a new W3C
or IETF standard.
