# ADR-0001: Memory is separate from execution

Status: Accepted for prototype · 2026-11-09

## Context

Durable workflow engines record command progress, but execution replay is not a source of physical truth. Agent long-term memory includes claims with provenance and temporal validity; telemetry and media have different write and retention characteristics.

## Decision

1. Memory core manages sourced assertions, corrections and retrieval. It does **not** issue device commands.
2. `valid_time` vs `recorded/known_time` remain separate. A recorded correction does not rewrite past knowledge.
3. Objects are content-addressed and referred to by SHA-256. The object store is separate from metadata transaction boundaries.
4. Orchestrators and device gateways are consumers/producers through explicit adapters.
5. Authoritative device identity, authorization, and final physical success belong to the action policy / device evidence subsystem, not to semantic retrieval.
6. SQLite is a reference backend, not a commitment to one long-term storage vendor.

## Follow-up decisions

- Define authorization boundary and per-tenant encryption, erasure and retention policies before production.
- Add PostgreSQL adapter and objectively benchmark pgvector, graph and time-series queries.
- Add event-version and observation clock/sequence semantics before declaring any physical-state correctness.
- Make adapters for Temporal/Restate emit evidence references but avoid mirroring their internal histories.

## Trade-offs

One logical memory contract with replaceable physical backends creates adapter work. This is preferable to making our memory semantics depend on an execution framework or one database extension.
