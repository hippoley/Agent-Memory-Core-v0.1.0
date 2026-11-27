# AMC Continuity Ledger Conformance Probe v1

This is a deliberately **standalone, framework-neutral, Python stdlib-only**
consumer of two public GET responses. A developer can copy just
`tools/verify_continuity_receipt.py` to another repository without installing
AMC or connecting to its database.

Input JSON structure:
```json
{
  "task": {
    "task_id": "11111111-1111-4111-8111-111111111111",
    "task_type": "continuity.effect.v1",
    "status": "completed",
    "input": {"required_effect_keys": ["device-effect-v1"]}
  },
  "effects": {
    "task_id": "11111111-1111-4111-8111-111111111111",
    "items": [{
      "effect_key": "device-effect-v1",
      "request_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "status": "committed",
      "attempt_count": 1,
      "provider_reference_recorded": true,
      "physical_independence_verified": false,
      "causal_effect_verified": false
    }]
  }
}
```

Run after saving the task and effects GET responses **from the same tenant and
observation window** into the `task` and `effects` keys:
```bash
python tools/verify_continuity_receipt.py continuity-bundle.json
# exit 0=PASS, 1=FAIL, 2=UNKNOWN
```

Result scope: **only local consistency of untrusted JSON**. The probe checks
that every required Effect Key exists and is committed, no unresolved effects
are concealed, task IDs align, and hashes/statuses are structurally plausible.
It cannot know whether the JSON was forged, responses were captured atomically,
the worker had authorization, an external provider actually committed, or a
physical device changed. `PASS` is always labeled `LEDGER_CONSISTENT_ONLY`.
No signatures, physical provenance or causal evidence are implied.

This is intended as a minimal reproducible test vector for third-party Agent
framework maintainers. It is not yet an independent standard, an upstream
endorsement, or proof of external adoption. Extend it with authenticated and
signed evidence only when such evidence actually exists.


## v1 supported profiles and malformed input

The checker recognizes both `continuity.effect.v1` and the creator-gated
`continuity.authorized-effect.v1` profile **for ledger consistency only**.
Even for the creator-gated profile, the returned
`actor_authorization_verified` is ALWAYS `false`: this offline tool has no
source of independently authenticated Principal or gateway permission evidence.

The CLI rejects duplicate JSON object keys and non-standard `NaN` /
`Infinity` literals rather than silently normalizing conflicting data.
Malformed enum values (including arrays/objects) fail with reason codes
instead of uncaught exceptions. CLI exit codes remain 0 / 1 / 2.
