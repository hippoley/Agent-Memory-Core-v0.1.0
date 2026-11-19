# Durable Evidence Trust Foundation

**PR:** #55 · Draft  
**Scope:** producer identity, four-dimensional authority, attestation verification, freshness, replay prevention and semantic fact deduplication.  
**Explicitly out of scope:** live evidence collection, Temporal History/Heartbeat collection, Provider adapters, automatic recovery.

## 1. Trust is not one property

| Property | Verification question | Mechanism |
|---|---|---|
| Integrity | Was this exact envelope changed? | Canonical SHA-256 |
| Authenticity | Which key signed this statement? | Ed25519 |
| Principal status | Is the signing key still attached to an active service identity? | Principal + key registry lookup |
| Authority | May this producer assert this claim for this source and scope? | Four-dimensional authority grant |
| Freshness | Is the attestation and observation within policy? | Expiry, max lifetime, observation age and clock-skew checks |
| Replay resistance | Has this exact attestation nonce already been consumed? | Transactional nonce ledger |
| Fact identity | Is this the same fact under a new nonce, or conflicting content? | Semantic fact ledger |
| Completeness | Have all required evidence sources been observed? | Not established by this PR; collection manifest belongs to PR #56 |

A signature proves that a trusted key signed a statement. It does not prove that the producer's observation is truthful, that the producer's system was uncompromised, or that the producer has authority over every claim in its payload.

## 2. Four-dimensional Evidence Authority Matrix

Every accepted attestation must match a grant across all four dimensions:

`Producer × Evidence Source × Claim Type × Authority Scope`

| Dimension | Bound to | Example |
|---|---|---|
| Producer | Issuer + key ID + active service Principal | `worker-prod-7 / key-2026-01` |
| Evidence source/type | Envelope `source` | `activity_heartbeat` |
| Claim type | Signed attestation `claim_type` | `worker.heartbeat_observed` |
| Authority scope | Tenant + task type | `tenant-a / ventilation.control` |

A grant is stored in `memory_evidence_authority_grants`; it is not inferred from the producer's role or from a payload boolean. Tenant and task-type scopes may use an explicit `*` wildcard, which must be deliberately provisioned. Exact tenant/task-type grants take precedence over wildcard grants.

### Claim authority examples

| Producer/source | Permitted claim example | Not implied |
|---|---|---|
| PostgreSQL / `postgres_task` | `task.business_status_observed` | Provider side effect committed |
| Temporal / `temporal_history` | `workflow.execution_status` | Physical device completed action |
| Worker / `activity_heartbeat` | `worker.heartbeat_observed` | Provider durably accepted request |
| Provider / `provider_receipt` | `provider.effect_committed` | Whole Agent task is correct |
| Independent verifier | `evidence.bundle_verified` | An unobserved physical fact |

These are protocol claim names, not automatic grants. Each key must receive explicit grant rows.

### The authoritative flag

`payload.authoritative=true` is untrusted input. It is accepted only when the matching grant explicitly has `allow_authoritative=true`. Otherwise verification fails closed. The flag does not upgrade a claim beyond the source and claim type permitted by the grant.

## 3. Database model

Migration `010_evidence_provenance.sql` adds four tables:

- `memory_evidence_producer_keys`: issuer/key ID, raw Ed25519 public key, mandatory service Principal, audience, validity and revocation.
- `memory_evidence_authority_grants`: normalized Producer × Source × Claim Type × Tenant/Task-Type scope grants, including the authoritative-claim bit.
- `memory_evidence_nonces`: one-time attestation nonce consumption.
- `memory_evidence_facts`: semantic fact identity independent of attestation nonce and envelope observation time.

Private keys are never stored in PostgreSQL. A producer key is bound to an active Principal of type `service`; the tenant must also be active. Principal deletion is restricted while a producer key references it.

## 4. Attestation

Schema: `amc.evidence-attestation.v1`.

The Ed25519 signature binds:
- issuer, key ID and audience;
- issued-at, expiry and nonce;
- exact envelope SHA-256;
- tenant, task, source and claim type;
- Workflow ID, Run ID and Lease Epoch.

Changing the claim type or any bound identity requires a new signature. Canonical JSON uses sorted keys, compact separators, UTF-8 and rejects non-finite numbers.

## 5. Verification and revocation linearization

`verify_and_consume_evidence(...)` performs the following in one PostgreSQL transaction:

1. Validate envelope digest, schema and attestation binding.
2. Validate audience, timestamp policy and observation freshness.
3. Lock the producer key, service Principal and tenant with `FOR SHARE`.
4. Check active Principal, active tenant, key validity and revocation.
5. Find and lock a matching four-dimensional authority grant.
6. Verify Ed25519.
7. Insert the nonce, rejecting an already-consumed nonce.
8. Insert or compare the semantic fact identity.

A concurrent key revocation takes an update lock on the same producer-key row. If verification acquired its share lock first, verification linearizes before revocation and the revocation waits for that transaction. If revocation commits first, a later verification observes the revoked key and fails. The PostgreSQL integration test exercises this ordering.

## 6. Replay, duplicate fact and semantic conflict

These are intentionally distinct outcomes:

| Case | Definition | Result |
|---|---|---|
| Attestation replay | Same issuer/key/nonce submitted again | `EvidenceReplayError` |
| Evidence duplication | Same semantic fact, re-attested with a fresh nonce or envelope timestamp | Accepted as `fact_disposition=DUPLICATE`; no second fact is created |
| Semantic conflict | Same semantic fact identity but different canonical claim payload | `EvidenceFactConflictError`; transaction rolls back |

Semantic identity is keyed by tenant, task, source, claim type, Workflow, Run, Epoch and a stable `fact_id` (or source event/receipt ID). When no stable event ID exists, the empty identity is conservative: different payloads for that same identity conflict rather than silently becoming separate facts. Producers should supply immutable source event IDs for event streams.

Nonce uniqueness prevents reuse of one attestation. The fact ledger prevents the same immutable fact from being reintroduced with a new nonce. Neither substitutes for the other.

## 7. Trusted adjudication

`adjudicate_trusted_evidence(...)` verifies every signature and authority grant, consumes nonces and records semantic facts inside one outer transaction. The pure adjudicator receives only verified envelopes. Any invalid signature, unauthorized claim, replay or semantic conflict aborts the bundle transaction.

A valid attestation does not authorize a retry, task completion, lease mutation or external side effect. Recovery authorization remains a separate protocol decision.

## 8. PR #55 acceptance boundary

Included:
- Producer service Principal binding and active tenant check.
- Ed25519 signing and verification.
- Four-dimensional source/claim/tenant/task-type grants.
- Explicit authority for `authoritative=true`.
- Attestation freshness and replay defense.
- Semantic fact duplicate/conflict handling.
- Unit tests and real PostgreSQL revocation/verification concurrency test.

Moved to PR #56:
- PostgreSQL/Temporal live collectors.
- Temporal History and Activity Heartbeat collection.
- Provider receipt adapters.
- Signed collection manifest and completeness proofs.

Moved to PR #57:
- Cross-system adversarial fault injection and independent conformance suite.

This PR is a trust foundation, not a complete evidence collection network. No automatic recovery is performed.
