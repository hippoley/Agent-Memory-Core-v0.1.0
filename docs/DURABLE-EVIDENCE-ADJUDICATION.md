# Durable Execution Evidence & Adjudication Protocol

**Status:** Draft protocol / pure reference implementation  
**Schema:** `amc.durable-evidence.v1`  
**Dependency baseline:** PR #53 candidate `b67392b22e1f444d0c09626895975b82feebd900`  
**Safety boundary:** observation and adjudication only; no automatic repair

## 1. Why this protocol exists

A Temporal Workflow status is not a business outcome. A PostgreSQL task status is not proof that a remote provider performed an operation. A worker lease is authority to attempt a fenced state transition, not proof that an external side effect did or did not happen.

The protocol therefore keeps three domains separate:

1. **Execution evidence:** Temporal history, Workflow lookup, Activity heartbeat.
2. **Business and ownership evidence:** PostgreSQL task state, dispatch ledger, current lease epoch.
3. **External-effect evidence:** PostgreSQL Effect Ledger and authoritative provider receipts.

No single status field is allowed to overwrite facts from another domain.

## 2. Evidence Envelope

Every observation is represented as an immutable, tenant- and task-scoped envelope:

```json
{
  "schema_version": "amc.durable-evidence.v1",
  "evidence_id": "evt-01",
  "tenant_id": "tenant-a",
  "task_id": "task-a",
  "source": "temporal_history",
  "observed_at": "2026-11-19T10:00:00Z",
  "workflow_id": "workflow-a",
  "run_id": "run-a",
  "lease_epoch": null,
  "payload": {
    "execution_status": "RUNNING"
  },
  "sha256": "<sha256-of-canonical-unsigned-envelope>"
}
```

The digest is SHA-256 over canonical UTF-8 JSON of all envelope fields except `sha256`, using sorted keys, compact separators, UTF-8 characters, and no NaN/Infinity values. The digest detects accidental or unauthorized mutation after capture; it is not a digital signature and does not establish who produced the evidence. Production transport must authenticate evidence producers and preserve provenance.

Supported sources:

- `postgres_task`
- `postgres_dispatch`
- `temporal_history`
- `temporal_lookup`
- `activity_heartbeat`
- `effect_ledger`
- `provider_receipt`

All timestamps must include an explicit timezone. Tenant ID and Task ID are mandatory. Workflow ID, Run ID, and Lease Epoch are retained when applicable.

## 3. Domain-specific precedence

There is deliberately no universal "latest status wins" rule.

| Question | Primary evidence | Constraints |
|---|---|---|
| Did a Workflow execute? | Temporal History / explicit Temporal lookup | A transport error is not NOT_FOUND |
| Is a business task complete? | PostgreSQL task row | Temporal COMPLETED cannot complete the business row |
| Who may commit a state transition? | Current PostgreSQL lease + Epoch | A stale epoch never regains authority |
| Did an external operation happen? | Authoritative provider receipt | Must be tied to the same effect and idempotency key |
| What does the application currently believe? | PostgreSQL Effect Ledger | UNKNOWN remains unresolved absent independent evidence |

Provider receipts only resolve an effect when the receipt is marked authoritative and carries an idempotency key. A receipt that contradicts a terminal Effect Ledger state, or refers to an effect with no matching intent, produces CONFLICT rather than silently overwriting either record.

Evidence inventory completeness matters. A task's declared `effect_count` must match the supplied Effect Ledger records before the adjudicator can confirm completion or authorize a start. Missing evidence is not evidence of no effects.

## 4. Adjudication outcomes

| Outcome | Meaning | Recovery authorization |
|---|---|---|
| `CONFIRMED` | Temporal completion, PostgreSQL business completion, and complete resolved effect inventory agree | DENY; no recovery needed |
| `NOT_STARTED` | Explicit Temporal NOT_FOUND, valid current dispatch lease, nonterminal task, and complete effect inventory with no unresolved operation | ALLOW conditionally for START_OR_RECOVER_TEMPORAL |
| `IN_PROGRESS` | Temporal execution, fresh Activity heartbeat, or effect execution/reconciliation is active | DENY |
| `UNKNOWN` | Evidence is missing, stale, incomplete, or insufficient to determine the outcome | DENY |
| `CONFLICT` | Identity, Run ID, provider receipt, or cross-domain facts contradict | DENY |

The conditional `ALLOW` is not an execution command. The caller must recheck the exact current lease owner and Epoch atomically at the mutation boundary. The reference implementation never opens a database connection, calls Temporal, or calls a provider.

## 5. Non-goals and safety invariants

- No automatic retry of UNKNOWN.
- No automatic task completion from Temporal COMPLETED.
- No inference of Workflow absence from UNAVAILABLE, timeout, or client exceptions.
- No effect retry from a local timeout or missing Activity heartbeat.
- No lease extension, claim, ACK, cancellation, compensation, or provider call.
- No cross-tenant evidence aggregation.
- No digest treated as identity authentication or non-repudiation.

## 6. Reference implementation

`src/agent_memory_core/durable_evidence.py` provides:

- `EvidenceEnvelope.create(...)`: create a canonical digest-protected envelope.
- `EvidenceEnvelope.from_dict(...)`: parse and verify a serialized envelope.
- `EvidenceEnvelope.verify()`: validate schema, timestamp, scope, and digest.
- `adjudicate_evidence([...])`: deterministic, pure adjudication.
- `Adjudication.to_dict()`: machine-readable result with evidence digests and reasons.

The implementation is intentionally a pure function. Source adapters that collect live PostgreSQL, Temporal, heartbeat, and provider evidence are a separate future layer.

## 7. Conformance tests and open gates

The initial tests cover digest tampering, timezone validation, cross-tenant identity conflict, Run ID divergence, active execution, fresh heartbeat, unresolved effects, authoritative and unverified receipts, contradictory receipts, conditional NOT_STARTED authorization, missing lease, incomplete effect inventory, and orphan receipts.

Still required before production claims:

1. Bind each source adapter to independently authenticated real evidence.
2. Define freshness windows and clock-skew bounds per source.
3. Bind provider receipts to provider-specific signature/lookup guarantees and idempotency-key derivation.
4. Add property-based and adversarial ordering tests, including duplicate and delayed evidence.
5. Persist signed evidence bundles and independently verify them in a clean-room Conformance Kit.
6. Validate authorization against the real PostgreSQL lease row inside the same transaction that commits the recovery intent.

Until those gates pass, this is a draft protocol and reference adjudicator, not production recovery authorization.
