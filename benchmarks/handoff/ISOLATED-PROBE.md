# US-C01: independent Agent handoff with fenced Provider execution (experimental)

This is a **real PostgreSQL + AMC HTTP + independent subprocess +
SQLite-backed HTTP Provider** acceptance probe. It is not a production
cross-provider exactly-once guarantee, a real two-LLM benchmark or a Mem0
comparison. This page describes the **current** head behavior; prior
iteration notes are recorded in PR #62.

## End-to-end authority and sequence

1. **Agent A** (separate process, public AMC SDK) writes two source-linked
   supplier policy claims and creates a task and immutable handoff. It exits.
2. **Agent B** (another process, public AMC SDK) accepts the handoff and derives
   a bounded decision. It has no Provider executor token and **never sends a
   Provider POST**. Without registered intent or a committed receipt, it fails
   closed.
3. **Fenced Worker** (`fenced_worker.py`, test-only PostgreSQL authority)
   claims the task using AMC's existing Worker lease, atomically commits
   checkpoint + effect intent + Outbox, and calls `begin_execution`.
4. **Provider executor** (`dispatch_worker.py`) checks the active Worker ID,
   epoch, `EXECUTING` ownership and canonical request digest before sending
   one authenticated POST using a **dedicated Provider secret**. Agent B does
   not receive this secret.
5. **External Provider** (`isolated_run.py provider`) independently journals
   the business effect in SQLite. It requires `PROVIDER_EFFECT_TOKEN` for
   POST and supports read-only committed receipt lookup by request ID.
6. **Recovery Worker** (`recover_worker.py`) uses **test-only** expiration of
   the old lease; claims a higher epoch, marks stale `EXECUTING` as
   `UNKNOWN`, moves to `RECONCILING`, verifies receipt + canonical request
   SHA-256 and uses AMC's existing fenced transition to `COMMITTED`. Then
   it completes the durable task. Missing/conflicting receipts do not commit.
7. **Agent B** re-enters independently, checks the frozen handoff and current
   intent, reads the Provider receipt, then idempotently persists the decision.
   The independent observer remains **fail-closed** for scoring until its
   complete evidence/attestation contract is separately proven.

## Precisely controlled crash injection

`tests/test_handoff_real_process.py` has both clean and real SIGKILL cases.
In the SIGKILL case the Provider commits its SQLite transaction, then holds the
HTTP response; the supervisor verifies that it committed and **kills the actual
Provider executor**, not Agent B. A distinct fenced recovery process
reconciles the committed effect. Tests require:

- Provider effect journal has **one POST and one executed action**.
- A forged/uncredentialed Agent B POST receives **403**, and a pre-receipt
  Agent B cannot claim completion.
- A stale Worker epoch or wrong tenant cannot dispatch an effect.
- The recovered task and AMC effect become COMMITTED only from the current
  fenced Worker; **two concurrent Agent B receiver processes** converge on
  one canonical `handoff_decision` through AMC's idempotent memory API.
- Intent and external receipt must have the same request ID, effect key,
  business payload and request digest.

Run the live acceptance with a disposable PostgreSQL stack:

```bash
MEMORY_TEST_DSN='postgresql://...' python -m pytest -q -rs tests/test_handoff_real_process.py
```

Run focused no-database tests:

```bash
python -m pytest -q tests/test_handoff_isolated_run.py tests/test_handoff_effect_reconciliation.py tests/test_handoff_benchmark.py
```

Do not use the example Provider in production. The demo's Provider bearer
credential and read-only receipt are not independently signed, the recovery
harness force-expires an old Worker lease in the test database, and there is
no production credential-delegation, durable executor supervision, remote
fencing enforcement or real LLM/Mem0 comparison. A successful integration
test demonstrates the **specified experiment**, not universal exactly-once
delivery or a 9.5/10 product score. PR #62 remains Draft.
