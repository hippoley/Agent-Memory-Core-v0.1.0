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
`TrustedSource`. The module's SHA-256 verifies consistency of that
snapshot in this call; it is **not** a signature or proof that the
original source was authentic. Trust anchors and authenticity verification
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
