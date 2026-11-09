# Roadmap and evidence gates

| Gate | Work | Acceptance |
| --- | --- | --- |
| G0 | Time/source/object reference kernel | Unit tests green, no cloud dependency |
| G1 | Tenant auth, encryption, erase/redaction semantics, recovery | Threat model + adversarial tests |
| G2 | Typed schemas, device & space IDs, sensor clock/sequence | Historical replay and stale-observation rejection |
| G3 | PostgreSQL & pgvector backend | Semantic parity + measured Recall@k and P95 |
| G4 | Graph and time-series optional adapters | Real workloads prove gain versus SQL baseline |
| G5 | Temporal, Restate, WindowPilot connectors | No false physical-success claim in fault tests |
| G6 | Interop export/import + provenance conformance | Cross-runtime independent reproducibility |

Not planned: replacing Temporal, implementing a general LLM framework, or manufacturing a new DB engine.
