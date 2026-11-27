# US-C01 · Source authority and fenced intent share one transaction

Previously `verified_fenced_worker.py` validated the signed v3 source
against PostgreSQL, **closed the verifier connection**, and subsequently
called `fenced_worker.register_intent()` with a new database connection.
The `FOR SHARE` source-revision and producer-key locks were therefore
released before the fenced intent was committed.

In the experimental verified Worker entrypoint, `register_intent` now accepts
a trusted `source_gate(conn)` callback and executes it **inside the same
top-level PostgreSQL transaction** as `claim`, `commit_checkpoint`, and
`begin_execution`. `review_registry_handoff_claim` uses a nested transaction;
its row/key locks remain held by the outer transaction until commit.
A denied/stale source causes rollback before writing an effect intent.
The caller still needs to obtain the accepted handoff through authenticated
AMC API, not arbitrary Agent JSON. The source gate's `plan` result
is *knowledge provenance only*, not device or payment authorization.

This removes the specific **source-check-to-DB-intent** window in this one
composed benchmark entrypoint. It does NOT:
- make direct calls to the generic `fenced_worker.register_intent` automatically
  source-authorized (that entrypoint remains an explicit diagnostic fixture);
- eliminate the post-intent/pre-remote-Provider revocation window;
- provide independent Agent B / physical Provider delegation;
- replace admin `MEMORY_TEST_DSN` with a production least-privilege worker;
- demonstrate a deterministic concurrent revocation barrier with independent
  PostgreSQL connections yet (see Issue #67).

Do not close US-C01 #67 or label this production-safe until a deterministic
concurrent revoke-before-intent test, restricted-login tests, and live Provider
admission enforcement all pass. A green unit test alone is not those proofs.
