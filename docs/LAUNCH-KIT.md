# Open-source launch kit — quality over hype

This is a **maintainer checklist**, not evidence that the actions below have already occurred.

## Recommended repository metadata (GitHub → About)

**Description (≤160 chars):**

> Memory that survives being wrong. A local-first system of record for evolving agent beliefs — with bitemporal history, provenance and hybrid retrieval.

**Suggested topics:**

`agent-memory` · `ai-agents` · `long-term-memory` · `postgresql` · `pgvector` · `bitemporal` · `knowledge-graph` · `local-first`

Use only topics that reflect real implemented behavior. Do not add speculative product or performance claims.

## Social preview and homepage

- Source artwork: `docs/assets/hero-belief-lifecycle.svg`. Export an appropriately cropped PNG for **GitHub social preview** manually; GitHub's repository settings are not changed by this PR.
- Homepage: publish `docs/demo/` through GitHub Pages **after merge and manual enablement**. Do not enter credentials into the static synthetic demo.
- Short link: use the verified Pages URL only after actually opening it in an incognito browser.
- Add a brief authentic GIF/screencast only after recording the working local Console or a real API-backed correction sequence. A synthetic animation must be labeled synthetic.

## First-run validation before public launch

- [ ] Clone a clean checkout on a new machine and complete Quick Start with no prior env state.
- [ ] Verify local Swagger, Console and an authorized write/recall/correction path.
- [ ] Open every README relative link and inspect SVG rendering in both GitHub light and dark modes.
- [ ] Verify CI at one immutable commit and repair failures before merging.
- [ ] Publish actual host, hardware, dataset and commands alongside any performance numbers.
- [ ] Check that examples and screenshots contain no passwords, bearer tokens or personal memory records.
- [ ] Configure GitHub repository About, topics, social preview and Pages — these settings are outside this PR.
- [ ] Publish a tagged pre-release only when release gates are accurately described.
- [ ] Enable GitHub Discussions if maintainer response capacity exists; otherwise issues and PRs are enough.

## What good community feedback looks like

A high-quality issue provides: *input fact → correction or historical query → expected canonical behavior → observed response → reproduction environment → test evidence*. Recruit reproducible counterexamples, not hollow stars.

## Positioning guardrails

1. **Lead with belief revision**; do not lead with a long list of storage libraries.
2. **Demonstrate provenance and time** with a tiny, repeatable scenario.
3. **Treat 'lightweight' as an architectural direction** until RAM/CPU/disk/latency comparisons are measured.
4. **State open gates openly**: natural-language extraction quality, retrieval relevance and production security.
5. **Never assert superiority over Mem0, LangGraph or vector databases** without a fair controlled comparison.

The most shareable technical story is a real failure that ordinary retrieval mishandles, followed by a minimal, independently verifiable correction and evidence trace.
