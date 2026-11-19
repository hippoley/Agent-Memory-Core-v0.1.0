# Durable Evidence Provenance & Live Collection

**Status:** Draft implementation / stacked PR #55  
**Base:** PR #54 candidate `4b4549abba66e81e534cf200cc1dc78c0c0a758a`  
**Safety boundary:** read-only collection and evidence verification; no automatic recovery

## 1. Trust model

A digest, a signature, and an authoritative fact are different things.

| Property | What this slice establishes | What it does not establish |
|---|---|---|
| Integrity | Canonical SHA-256 detects envelope mutation | Who created the envelope |
| Authenticity | Ed25519 signature verifies against a trusted public key | That the producer's underlying observation was truthful |
| Authority | Registry constrains issuer/key to source, tenant and audience | Authority beyond the explicitly registered scope |
| Freshness | Attestation expiry, maximum lifetime, observation age and clock skew are checked | A globally trusted clock or perfect source timestamp |
| Completeness | Collector reports truncation and enumerates source gaps | A complete cross-system snapshot or all provider facts |

A signature proves that the holder of a trusted private key signed a particular statement. It does not prove that the statement itself is true. A provider receipt therefore requires a provider-specific trust relationship and adapter; a local `provider_result` JSON field is not an independent receipt.

## 2. Producer key registry

Migration `010_evidence_provenance.sql` adds:

- `memory_evidence_producer_keys`: issuer + key ID, raw Ed25519 public key, mandatory service Principal, allowed sources, allowed tenants, audience, validity window and revocation timestamp.
- `memory_evidence_nonces`: unique issuer/key/nonce consumption ledger with evidence digest and expiry.

Only public keys are stored. Private keys must remain with the producer, preferably in a KMS/HSM. Registry writes are administrative operations and are not exposed as a public self-service API in this slice. A producer key cannot outlive the identity anchor by deleting its Principal: the foreign key uses RESTRICT.

A key is accepted only when:
- It exists, is not revoked, and is bound to an active Principal of type `service`. The evidence tenant must also exist and be active.
- The attestation audience matches both the caller's expected audience and the registry.
- The source and tenant are explicitly authorized (or tenant `*` is deliberately provisioned).
- Issuance is within the key validity window.
- The Ed25519 signature verifies over canonical attestation claims bound to the exact evidence digest, tenant, task, source, Workflow ID, Run ID and Lease Epoch.

## 3. Attestation format

`amc.evidence-attestation.v1` signs these claims:

```json
{
  "schema_version": "amc.evidence-attestation.v1",
  "issuer": "https://producer.example",
  "key_id": "worker-key-2026-01",
  "audience": "amc-evidence-ingest",
  "issued_at": "2026-11-19T12:00:00Z",
  "expires_at": "2026-11-19T12:02:00Z",
  "nonce": "a-unique-random-value-at-least-16-chars",
  "evidence_sha256": "<evidence-envelope-sha256>",
  "tenant_id": "tenant-a",
  "task_id": "task-a",
  "source": "temporal_lookup",
  "workflow_id": "workflow-a",
  "run_id": "run-a",
  "lease_epoch": 7,
  "signature": "<base64url-ed25519-signature>"
}
```

Canonical JSON uses sorted keys, compact separators, UTF-8 and rejects non-finite numbers. The signature is over every claim except `signature`. The attestation binds to the exact evidence envelope; it cannot be transplanted to another task, tenant, Workflow, Run, source or Epoch.

## 4. Verification and replay prevention

`verify_and_consume_evidence(conn, evidence, attestation, ...)`:

1. Validates the evidence digest and schema.
2. Validates attestation schema, audience, identity binding and timestamp format.
3. Enforces maximum attestation lifetime, observation age and clock skew.
4. Locks the trusted key row `FOR SHARE` and checks revocation, validity and authority.
5. Verifies Ed25519.
6. Inserts the nonce under a unique primary key in the same transaction.

A duplicate nonce is rejected. Key revocation updates serialize against in-flight verification through the row lock. Nonce rows are retained until explicit retention cleanup; cleanup policy and operational tooling remain a follow-up.

`adjudicate_trusted_evidence(...)` verifies every item and consumes all nonces in one outer transaction. If any item fails, the evidence bundle is not adjudicated and the nonce transaction rolls back.

## 5. Live collector

`collect_live_temporal_dispatch_evidence(conn, client, tenant_id, limit)` reads:

- PostgreSQL durable task status and task/dispatch Workflow ID relationship.
- Dispatch state, owner, Epoch, lease expiry and recorded Run ID.
- Durable task worker lease owner, Epoch, expiry and heartbeat timestamp.
- Effect Ledger status, request digest, attempt count, execution owner/Epoch, provider reference and deterministic idempotency key.
- Temporal `describe()` metadata: execution status, Workflow Type, Task Queue, Memo identity and Run ID.

Temporal `describe()` is represented as `temporal_lookup`. It is not mislabeled as full Workflow History.

The returned `EvidenceCollection` reports whether the dispatch-row scope was truncated, lists the sources collected and explicitly lists known gaps. Call `require_complete()` before treating the returned dispatch-row set as complete.

Known gaps in this first collector:
- Full Temporal Workflow History is not fetched.
- Temporal Activity Heartbeat records are not fetched.
- Provider receipts are not fetched or fabricated from local `provider_result`.
- Tasks without a Temporal dispatch-ledger row are outside this dispatch-scoped query.
- PostgreSQL and Temporal are not read under one distributed atomic snapshot; each Temporal observation has its own capture time.

Transport errors are recorded as `lookup_status=ERROR`; only an explicit Temporal `NOT_FOUND` is represented as `NOT_FOUND`.

## 6. API usage

Install with the evidence and database extras:

```bash
pip install -e '.[postgres,temporal,evidence]'
```

Create and sign an envelope in the producer process:

```python
proof = sign_evidence(
    envelope,
    issuer=producer_issuer,
    key_id=producer_key_id,
    audience="amc-evidence-ingest",
    issued_at=issued_at,
    expires_at=expires_at,
    nonce=cryptographically_random_nonce,
    private_key=producer_private_key,
)
```

Verify and adjudicate a complete signed bundle:

```python
trusted_result = adjudicate_trusted_evidence(
    control_plane_connection,
    [(envelope, proof)],
    expected_audience="amc-evidence-ingest",
)
```

The private key is never passed to the verifier and is never stored in PostgreSQL.

## 7. Remaining production gates

1. Replace manual key provisioning with an audited control-plane operation and explicit rotation/revocation workflow.
2. Define source-specific freshness and clock trust policies; the default 15-minute observation age is not universal.
3. Fetch actual Temporal History and Activity Heartbeats, retaining event IDs and server timestamps.
4. Add provider-specific receipt adapters with independently verifiable signatures or authenticated lookup semantics.
5. Add a durable, signed collection manifest with query watermark, pagination cursor, expected source inventory and missing-source reasons.
6. Test concurrent nonce replay and key revocation against real PostgreSQL.
7. Test the collector against real PostgreSQL + Temporal with lease changes, identity conflicts, missing dispatch rows and large tenant scopes.
8. Add retention/partitioning and bounded cleanup for nonce rows.
9. Keep recovery authorization separate; a valid signature does not itself authorize a retry or external side effect.

This is a draft trust and collection layer. It is not yet a production-grade provider evidence network or complete cross-system snapshot protocol.
