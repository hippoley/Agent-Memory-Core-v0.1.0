# Verified Learning Handoff · US-VL01 (experimental)

**Design input:** Anthropic Engineering, [The components of agent memory]
(https://www.anthropic.com/research/components-of-agent-memory),
published 2026-11-06. This is an independently designed AMC experiment;
it is **not** Claude Dreaming, and does not claim to replicate Anthropic's
models or benchmarks.

## What changed, and why

Anthropic describes session experience / transcripts, distilled memory notes,
memory representations (such as wikis), and a small injected context layer.
Its experiments show that (1) collecting more notes can be worse than
learning a small set of reusable strategies; (2) an erroneous note can
persist into later actions until checked against original evidence; and
(3) shared knowledge must preserve its original sources and avoid
destructive overwriting.

AMC already stores versioned, sourced claims and immutable work handoffs.
This experiment adds a **separate independent source-use gate** between
an accepted handoff and a future Agent's use of a learned claim.

```text
Agent A: observation -> model-proposed claim -> AMC versioned memory
                                               |
                                               v
                                immutable AMC context / handoff
                                               |
                                               v
Agent B: accepted handoff ----- Trusted source adapter
                         \          /
                          \        /
                         VerifiedLearning.review
                                  |
                         +--------+---------+
                         |                  |
                  verified for answer/plan  stale/conflict/unknown
                         |                  |
                         v                  v
                    read / plan            fail closed

                 execution always requires separate
                 Worker authority / receipts / fencing
```

The model decides what might be valuable to remember; **it does not
self-certify that its proposed rule is true**. The host must fetch and
authenticate an independent current document before constructing a
`TrustedSource`. The module also checks the accepted context pack's self-consistency SHA-256
before considering any claim. Both digests protect against accidental or
unsynchronized mutation, **not** issuer authentication. A malicious caller
can fabricate a new self-consistent packet or source; authenticating the
AMC HTTP response and the independent document remains the host's duty. Trust anchors and authenticity verification
remain the host's responsibility.

## API (pure read-only gate, no database migration)

```python
from agent_memory_core.verified_learning import TrustedSource, review_handoff_claim

# The trusted host (not the model or Agent B) obtains this document.
current = TrustedSource.from_verified_document(
    "supplier-contract:v3",
    {"payment_period": {"period": "quarterly"}},
)
# accepted is an actual response from AMC's authenticated accept_handoff API.
decision = review_handoff_claim(
    accepted_handoff=accepted,
    subject_id="supplier-001",
    predicate="payment_period",
    proposed_value={"period": "quarterly"},
    trusted_current_source=current,
    requested_use="plan",
)
assert decision.state == "VERIFIED"
assert decision.allowed_uses == ("plan",)
```

This function is an **application-side prototype**, not an independently
authenticated server-side authorization grant. The host must call the real
`accept_handoff` endpoint first. A compromised host could fabricate the
accepted response and current source. Do not send its decision to privileged
Provider tools without separately checking AMC's fenced Worker authorization.

## Deterministic proof obligations

| Gate | Expected behavior |
|---|---|
| Old handoff after actual memory revision | AMC rejects stale acceptance |
| Old source revision even if proposed value matches | `STALE`; require new handoff |
| Model-proposed answer differs from frozen claim | `DENIED` |
| Current independently supplied source disagrees | `CONFLICT` |
| Source is unavailable, unverifiable or corrupt | deny / unverified |
| Handoff is offered, incomplete or has UNKNOWN effect | deny |
| Source and accepted handoff match | allow answer/plan only |
| Request approval/payment/device control | never grant execution |
| Source changes after earlier validation | requires fresh review against current source |

```bash
python -m pytest -q tests/test_verified_learning.py
# With a local PostgreSQL development stack:
MEMORY_TEST_DSN='postgresql://...' python -m pytest -q -rs tests/test_verified_learning.py
```

The PostgreSQL integration uses the real AMC API to create v2, replace it
with v3, refuse the old frozen handoff, accept a fresh handoff and test the
source-use gate against the new claim. It makes **no Provider calls**.

## Release gate and what not to claim

1. This proves a *minimal deterministic* independent source-use contract.
   It does not establish general natural-language truth or model reasoning.
2. It does not implement a dreaming scheduler, a wiki, vector retrieval or
   auto-injection. These are separate model/harness strategies.
3. The source adapter is trusted by construction in this probe; real
   signatures, authentic source fetching and per-agent authorization are
   future production gates.
4. Do not treat an `allowed_uses` result as a token usable to execute a
   side effect; execution remains under PR #62's distinct authority boundary.
5. Before claiming competitive improvement, test a frozen multi-session
   corpus with real models and compare full transcript replay, Mem0, and
   memory consolidation/Dreaming-style retrieval at equal token budget.
   Measure correction propagation, stale-knowledge use, unsafe actions,
   false rejection, context tokens and end-to-end cost.

**Why this is an AMC-specific opportunity:** independently verified
reuse constraints and work execution authority survive changes of model
provider. Whether this offers a net gain over strong model-native memory
systems remains an empirical question.

## Authenticated source envelope — next trust-boundary probe

`src/agent_memory_core/source_attestation.py` adds an **opt-in**
Ed25519-verified source envelope. The signature covers issuer, tenant,
subject, predicate, exact source revision, claim value, issue time and
expiry. A host-managed `SourceTrustPolicy` separately pins authorized
issuer public keys, the currently active revision for each
tenant/subject/predicate tuple, and revoked issuer/source IDs. These
policy values **must not** come from an Agent prompt or envelope.

```text
Independent contract registry -- signs claim + revision + scope
                                  |
                                  v
                             Signed envelope
                                  |
 Host-owned key registry -------> + signature verification
 Host-owned active revisions ---> + currency / revocation check
                                  |
                                  v
                        Verified source comparison
                                  |
                   answer/plan only; no side-effect authority
```

Use `review_signed_handoff_claim(..., signed_source_envelope=...,
policy=host_trust_policy, tenant_id=...)` in place of the untrusted
`TrustedSource.from_verified_document(...)` fixture route. The old
`review_handoff_claim` remains a lower-level, application-side content
comparison and **must not** be treated as a production trust boundary.

Acceptance tests cover authorized Ed25519 signature; self-signed forged
data; changed signed content; unrecognized/revoked issuers; revoked or
non-current source revisions; wrong tenant/predicate; future issued
time; expiration; and the continued requirement that execution uses
separate Worker authority. The tests use ephemeral keys generated during
the run, not committed credentials.

**Still not production-complete:** this adapter receives the host's
public-key/revocation/current-revision snapshot as an argument.
There is no external authenticated source issuer service, immutable
source-fetch proof, live revocation subscription, durable registry/tenant
authorization, freshness guarantee on the policy snapshot, or
tamper-resistant audit receipt. The host is trusted and its own
authorization is not yet independently demonstrated; a malicious host
can supply a forged trust policy. Model A/B learning and competitor
benchmarks remain untested.

```bash
python -m pip install -e '.[dev,postgres,api]'
python -m pytest -q tests/test_source_attestation.py tests/test_verified_learning.py
```


## Independent TCP signer probe (controlled environment)

`benchmarks/handoff/independent_contract_issuer.py` serves a freshly
Ed25519-signed current contract revision over a **separate loopback TCP
process**. `tests/test_independent_contract_issuer.py` verifies the
attestation with a host-pinned *public* key, checks that a replacement
signature by an unrelated actor is rejected, and verifies that
knowledge acceptance cannot grant an approval action.

This demonstrates independent signer **process ownership**, not
production source authenticity: this test's harness explicitly creates
the private key and injects it into the issuer process; the HTTP
transport is loopback and unauthenticated, and the host still constructs
the trust policy in-process. It does **not** validate an external
contract registrar, source-document byte provenance, a durable
server-owned trust registry, revocation persistence, or a second
independent real LLM. Never describe it as a fully production-secure
issuer or a PR #62 combined execution acceptance.

Do **not** build another parallel trust registry. AMC already has
`evidence_trust.py` and PostgreSQL
`memory_evidence_producer_keys` / `memory_evidence_authority_grants`.
The production follow-on should reuse those existing issuer and service
principal authority primitives, adding a narrowly scoped knowledge
source revision registry with independently authorized updates.

```bash
python -m pytest -q tests/test_independent_contract_issuer.py
```

## PostgreSQL-owned source authority (incremental P0 slice)

`012_knowledge_source_revisions.sql` adds **only** the tenant/subject/predicate
current-revision authorization table. It does not duplicate
`memory_evidence_producer_keys` or `memory_principals`; its FK points
directly at the existing evidence producer-key registry. The table is a
control-plane object with no PUBLIC access. Production deployments must
use a restricted service role with least-privilege SELECT to validate
sources; the API and untrusted Agent principals must never be able to
edit revision authority.

`postgres_source_authority.review_registry_handoff_claim` reads the
current revision, pre-existing producer key and its owning *active service
principal*, active tenant, key audience `amc-knowledge-source`, key
validity and revocation **under PostgreSQL FOR SHARE locks**. It then
verifies the signed knowledge envelope against the database-owned key
while those locks remain held. Changing the current revision or revoking
the issuer invalidates a new evaluation; a previously returned read-only
decision is not a durable capability.

`tests/test_postgres_source_authority.py` includes deterministic negative
checks and a real-PostgreSQL test with a test-only admin creating an
issuer key and revision, then changing the revision and revoking the key.
It reuses the current signed-source contract and does not invoke
PR #62's execution path.

**Important remaining security caveat:** the registry-read function
accepts a `conn` supplied by its host. The security boundary is the
host's database principal and service deployment, not this function
signature. It does not yet prove a low-privilege dedicated read role,
authenticated external registrar or a genuine underlying document
signature. Model-untrusted code must not be given a raw database
connection, an editable trust policy, or execution capabilities.

```bash
python -m pytest -q tests/test_postgres_source_authority.py
MEMORY_TEST_DSN='postgresql://...' python -m pytest -q -rs tests/test_postgres_source_authority.py
```


## Operator-installed PostgreSQL privilege separation

An **optional** administrator-only SQL script
`sql/postgresql/security/003_knowledge_source_roles.sql` defines two
distinct NOLOGIN roles:

- `memory_source_verifier`: SELECT on source revisions, producer keys,
  principal and tenant status; **no** authority-table write privileges
- `memory_source_authority`: operator-managed source revision updates;
  **no** evidence producer-key write privileges

Neither role is installed during regular `PostgresMemory.migrate()`.
A database administrator must run the operator script and provision
separate verifier and authority identities. Never grant the authority
role to Agent or API credentials. Do not expose a database connection
with the authority role to an Agent.

`tests/test_source_authority_roles.py` verifies role design, and with
`MEMORY_TEST_DSN` tests server-enforced SQL privilege denial using
`SET LOCAL ROLE memory_source_verifier`; it must fail if the DB admin
cannot create the role (rather than marking the case successful).

This **does not** yet prove the real deployed service uses a dedicated
least-privilege LOGIN role. It also does not provide row-level isolation
for the verifier: it reads source authority across tenants, so this
role must only live in the trusted control plane and be bound to a
tenant-authorized request principal. A future release must demonstrate
its actual service credentials, prevention of privilege escalation,
and per-tenant authorization at the API boundary.

```bash
# Admin-only setup, never the tenant-facing API or LLM runtime
psql "$MEMORY_ADMIN_DSN" -v ON_ERROR_STOP=1 -f sql/postgresql/security/003_knowledge_source_roles.sql

# Unprivileged role acceptance; requires a PostgreSQL administrator test DB
MEMORY_TEST_DSN="postgresql://..." python -m pytest -q -rs tests/test_source_authority_roles.py
```
