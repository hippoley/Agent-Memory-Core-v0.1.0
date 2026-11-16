# Visual system · Bsph Blue / Editorial Computation

**Role:** the shared visual language for Agent Memory Core documentation, README illustrations and the experimental Memory Observatory UI. This file describes **design**; it does not claim end-to-end browser acceptance or actual knowledge-graph semantics.

## Palette

| Token | Hex | Purpose |
| --- | --- | --- |
| Paper | `#FBFCFF` | Main illustration background, light/dark GitHub compatibility |
| Sapphire / Bsph Blue | `#125CFF` | Primary focus, current belief, active flow, figure annotations |
| Deep Ink | `#091C4D` | High contrast headings and core-system labels |
| Supporting Ink | `#314766` | Secondary facts, captions and labels |
| Muted Text | `#7184A2` | Metadata and supplementary details |
| Hairline | `#DCE5F2` | Containers, grid, dividers, non-authoritative relationships |
| Tinted Surface | `#EDF3FF` | Canonical decision highlighting; never denote false measured confidence |

Typography: Inter, Helvetica Neue, Arial, local system fallback. Use no remote fonts, stock photos, proprietary visual assets or external scripts in README SVGs.

## Diagram grammar

- **White canvas, high whitespace, restrained linework**, consistent corner radius and a modular grid. Avoid dark green dashboards, thick 3D gradients, excessive glow, and unrelated visual palettes.
- **Sapphire** is reserved for key decisions and evidence-bound current state. Gray-blue represents metadata and secondary paths.
- A **solid connector** denotes the depicted primary path; **dashed** denotes optional, derived, or asynchronous flows. This is illustrative architecture, not an execution trace.
- **PostgreSQL is the sole canonical authority.** SQL/FTS, pgvector and Apache AGE are candidate retrieval channels and derived views, not independent sources of current fact truth.
- **Temporal** is a separate optional adapter for multi-step verification and durable workflows. Do not place it in the critical path for every `remember()` or claim production orchestration maturity.
- **Decorative filaments**, as in the hero or Observatory, denote an artistic motif or a domain-count visualization when explicitly labeled. Their proximity/crossing **must not** be described as a real AGE edge, vector similarity, causal path, or evidence score.
- Historical claims remain queryable; the visual correction example is synthetic and does not prove real-model extraction/revision accuracy.
- Screenshots must be generated from a rendered browser and checked in at a known version. Never substitute a generative mockup for a verified running application screenshot.

## Public README figures

| File | Subject | Truth boundary |
| --- | --- | --- |
| `docs/assets/hero-belief-lifecycle.svg` | Editorial product thesis: revised belief, preserved evidence | Illustrative fact and decorative filaments, not real tenant data |
| `docs/assets/architecture.svg` | Memory runtime and deployment responsibilities | Logical topology only; not a production security certification |
| `docs/assets/memory-lifecycle.svg` | Write, projection, and recall flow | Multi-candidate atomic revision and real-model generality are still release gates |
| `src/agent_memory_core/resources/dashboard/observatory.html` | Experimental UI in Draft PR #30 | Demo is synthetic; Live shows only loaded authorized API rows |

## README editorial rhythm

1. **15-second thesis:** what happens when an agent changes its mind?
2. **The visual proof point:** show the synthetic belief revision illustration as an illustration.
3. **30-second product explanation:** observability, time, provenance and current state — no fake “live graph” promises.
4. **5-minute local trial:** link an exact command and real local URL.
5. **System architecture:** one canonical record + derived indexes + optional AGE/Temporal.
6. **Evidence and invitations:** publish measured failures and open acceptance gates. Ask for reproducible issues, not unearned Stars.

## Accessibility and quality checks

- Every SVG uses a `viewBox`, `title` and `desc`, and is self-contained without fonts/CDN dependencies.
- Display important information as text labels; hue alone must not encode status.
- Validate XML and embedded README links in CI.
- Review layouts at 1280px and smaller. README figures are intentionally `viewBox`-scalable, but readable small mobile text is a follow-up visual QA gate.
- Keep terms and contrast consistent in English and Chinese documentation.

## Design versus release readiness

README visuals were authored in source SVG; passing XML tests does **not** mean cross-browser layout was inspected. The Observatory screenshot, public demo hosting, and its live-tenant UX remain separate verification tasks until accepted.
