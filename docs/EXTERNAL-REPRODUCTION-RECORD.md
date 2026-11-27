# AMC · Independent reproduction record (external consumers)

This is a **reporting template**, not a certification, endorsement or new
execution protocol. AMC maintainers' own CI does not count as non-author
reproduction. Do not paste secrets, bearer tokens, raw customer data, private
Endpoint URLs or identity credentials into a public GitHub Issue.

## 1. Scope: choose exactly one outcome

- [ ] **Public `main` user journey** — independent clean install, authenticated API/browser, write, revise, current/history, forget, stop/restart/readback
- [ ] **Experimental offline conformance** — 12 vector fixtures from **PR #69's exact recorded SHA**, separate from released `main`
- [ ] **Experimental multi-cloud** — PR #71 candidate adapter, clearly distinguish fake-client/real PostgreSQL/real-cloud evidence
- [ ] **Effect continuity** — only report what an independent Provider receipt and durable task ledger actually prove

## 2. Reproduce environment and exact commands

```text
Tester affiliation (optional; say if maintainer/author):
OS and version:
CPU/architecture:
Python version:
Docker/Compose version (if used):
Repository: hippoley/Agent-Memory-Core-v0.1.0
Branch:
git rev-parse HEAD:
Command(s), exactly as executed:
Exit code:
Elapsed wall time:
Was author intervention required? (yes/no; describe):
```

For the **public product**:

```sh
git clone --branch main --single-branch https://github.com/hippoley/Agent-Memory-Core-v0.1.0.git
cd Agent-Memory-Core-v0.1.0
git rev-parse HEAD
python scripts/demo_local.py
```

Use the install prerequisites in [README](../README.md) and the
[local demo guide](LOCAL-DEMO.zh-CN.md). Do not switch to a PR branch to
claim a public-main acceptance.

For the **experimental conformance verifier**, use the explicitly documented
SHA in [PR #69](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/pull/69)
and run `python -I -S tools/run_continuity_vectors.py`. It is **not
available on public main until merged**. A passing offline ledger fixture
never proves the authenticity or physical independence of the provided events.

## 3. Evidence ledger

| Item | PASS / FAIL / UNKNOWN | Evidence link or redacted path |
|---|---|---|
| Fresh clone of specified branch/commit | | |
| Installation without hidden steps | | |
| API readiness/authorization | | |
| Fact create/read/correction | | |
| `valid_at` and `known_at` historical consistency | | |
| Restart with preserved canonical data | | |
| Cloud index / remote provider genuinely used (if claimed) | | |
| Identified failed/unknown external effect (if claimed) | | |
| Proof of independent third-party operator (if claimed) | | |

**Evidence classification:** SOURCE = source code; CI = exact-SHA automated
test; LIVE = real external execution and separately retained outcome;
INDEPENDENT = non-author reproduction; ADOPTED = external project pinned a
dependency. Do not upgrade one class to the next without separate evidence.

## 4. Minimal issue report

```text
Expected:
Observed:
First unexpected step:
Is this a product fault, unclear documentation or an unverified dependency?
Evidence level: SOURCE / CI / LIVE / INDEPENDENT / ADOPTED
What this evidence DOES NOT prove:
Suggested smallest correction:
Link to the external project or independent implementation (if any):
```

Submit to [#57](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/issues/57)
for public `main` cold-install,
[#70](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/issues/70)
for independent conformance,
[#67](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/issues/67)
for source revocation before external effect admission, or
[#68](https://github.com/hippoley/Agent-Memory-Core-v0.1.0/issues/68)
for full verified work continuity.

A failing report with exact steps is valuable. It changes a product claim
into a reproducible engineering problem. Report `UNKNOWN` rather than
claiming safety from absent observations.
