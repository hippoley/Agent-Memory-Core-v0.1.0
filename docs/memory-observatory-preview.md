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
