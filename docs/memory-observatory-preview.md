# Memory Observatory · Bsph Blue (Preview)

This is an experimental **read-only** visualization of Agent Memory Core's canonical claims. It is intentionally separate from the existing ledger dashboard.

## Open

Start the existing FastAPI application as usual, then visit:

`http://127.0.0.1:8000/dashboard/observatory.html`

No frontend build step, JavaScript libraries, CDNs, or new backend endpoints are required. The existing classic dashboard remains available at `/dashboard/`.

## Visual mapping

The visual inspiration is data-driven generative graphics, not a reproduction of FIELD.IO's IBM Think code or artwork.

| Input | Mapping |
| --- | --- |
| Claim subject/predicate/source | Deterministic, disclosed domain classifier |
| Number of claims in a domain | Number of rendered fine-line strands (capped for performance) |
| Selected claim/domain | Bsph Blue highlight |
| Claim recorded timestamp | Snapshot slider and histogram |
| Source ID | Evidence detail inspector |
| Local text match | Domain highlight and local match count |

The layout uses deterministic procedural curves per domain. **Curve distance, intersection, direction and curvature carry no embedding similarity, knowledge graph, causal, or confidence semantics.** No fake edges, AGE neighbors, vector scores, quality measurements or production statistics are shown.

## Modes

- **Demo:** On first load, ten clearly marked synthetic claims. No backend connection required.
- **Live:** Click the arrow control at the top right, paste a bearer key, and connect to the **same-origin** API. Reads `GET /v1/memories?limit=500&include_superseded=true` and `GET /v1/events?limit=100`. The status and population caption explicitly distinguish live *loaded page counts* from full-tenant metrics.

The bearer token stays in in-memory JavaScript state. No URL keys, localStorage, third-party scripts, or outbound telemetry. Disconnecting to demo clears the token. Deploy the page only behind the same authentication/network controls used by your API; the HTML itself is publicly readable.

## Interaction

Use the domain picker to filter claims. Click a marked curve segment/label region to inspect a representative supporting claim and source. Search performs only a **local string filter over loaded claims**; it is not hybrid retrieval. The temporal slider filters claims by their available recorded timestamps. Ctrl+wheel zooms the topology; pause stops motion; reset restores the view. Export downloads the visible snapshot and loaded events as JSON.

## Known limits / v0.1 exit criteria

- Real AGE graph edges, vector distances, and cross-claim evidence paths need a purpose-built tenant-scoped read-only graph projection endpoint.
- Results are restricted to the first 500 claims and 100 events; large tenants need cursored pagination/aggregation, not an implied exhaustive count.
- Domain classification is a transparent heuristic; it is not a model-produced semantic taxonomy.
- Claim timestamp fallbacks depend on the API payload. Missing timestamps remain visible but cannot be accurately ordered historically.
- Click targets are domain-oriented in this prototype; production should offer exact strand-to-claim hit testing with stable IDs.
- Before a fundraising demo, run browser visual regression, keyboard/accessibility tests and a real-tenant API integration test; syntax validation is not equivalent to these.

## Design tokens

- Paper: `#fbfcff`
- Bsph Blue: `#125cff`
- Ink: `#182c4c`
- Lines: `#e5eaf4`
- Secondary text: `#8190aa`

Keep synthetic demo data labeled. Never present example metrics or visual curves as proof of retrieval correctness.

## Visual refinement · 2026-11-16

The second pass refines the frontend toward a computational-editorial, gallery-grade visual system:

- Off-white paper, fine grid lines, restrained sapphire blue and ink typography.
- Layered, volumetric *procedural filament bundles* instead of flat, single-wave graph curves.
- Fine perimeter instrumentation, slender annotation leaders, lighter panels and increased whitespace.
- A single throttled animation loop (approximately 30 FPS maximum), with reduced-motion support.
- Annotation and claim semantics remain distinct: the filament bundle denotes a classified claim domain, not a real graph cluster; curves and crossings imply neither semantic similarity nor relationships.

**Visual review limitation:** This change was syntax-checked against the committed HTML but not yet visually verified in a real browser session. Screenshots at 1440×900, 1920×1080 and narrow mobile viewport and manual controls testing remain pre-merge requirements. Do not claim visual acceptance based only on syntax validation.

## Public presentation deployment (manual action only)

The repository contains `.github/workflows/publish-memory-observatory-demo.yml`.
It deliberately uses **workflow_dispatch only** to avoid repeated GitHub Actions consumption.

1. Review and merge PR #30 after local/browser verification. GitHub Actions workflows are normally manually dispatchable from the default branch.
2. In GitHub **Settings → Pages**, set **Build and deployment → Source → GitHub Actions**.
3. Go to **Actions → Publish Memory Observatory Demo → Run workflow** and run it on `main`.
4. Confirm a successful `github-pages` environment deployment and copy the **actual deployment URL from the job output**. Do not infer success merely from this document.
5. Open the URL in an incognito browser and run the **LIVE DEMO** five-step story. The public static page is synthetic-only; the connect-live key entry is disabled there.

Expected GitHub Pages URL pattern **if** Pages is configured and deployed: `https://hippoley.github.io/Agent-Memory-Core-v0.1.0/`.
This is a prospective URL, **not verified online**.

For real data, run the same frontend on the protected API origin at `/dashboard/observatory.html` and provide tenant-scoped credentials there only. **Do not deploy live tenant credentials to Pages.**

### Presentation rehearsal checklist

- Browser widths: 1920, 1440, 768, 390.
- Demo explicitly displays SYNTHETIC DATA throughout guided mode.
- Five navigation steps each change the relevant visible claim state.
- Reset, pause, time-slider, domain filter, local search, selected evidence, JSON export work.
- Browser console is clean; no extra animation loops.
- The visual encoding is not represented as graph relationships or semantic similarity.

## Opt-in real Chromium acceptance

The repository includes `tests/test_memory_observatory_browser.py`, which serves the **actual committed HTML** on loopback and drives Chromium through the five-step tour, keyboard evidence selection, local search, temporal replay, JSON download, API success/401/empty response, Escape/focus restore and four viewport sizes. An optional screenshot directory captures 390, 768, 1440 and 1920-pixel layouts.

```bash
python -m pip install -e '.[dev,api]' playwright
python -m playwright install chromium
MEMORY_OBSERVATORY_BROWSER=1 pytest -q tests/test_memory_observatory_browser.py
MEMORY_OBSERVATORY_BROWSER=1 MEMORY_OBSERVATORY_SCREENSHOT_DIR=artifacts/screenshots pytest -q tests/test_memory_observatory_browser.py
```

When using system Chromium instead of the Playwright-managed browser, set `MEMORY_CHROMIUM_PATH` to its executable. Browser tests are **opt-in** and skipped in ordinary pytest/CI runs to avoid repeated browser downloads and Action expenditure. API responses are intercepted in browser tests to exercise UI states; `tests/test_observatory_live_postgres.py` separately verifies actual PostgreSQL read behavior and tenancy. Passing the browser suite is not evidence of a public Pages deployment or production security readiness.
