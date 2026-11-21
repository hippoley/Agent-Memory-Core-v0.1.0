# US-C01 · Agent Work Continuity (MVP)

**Product north star:** *Make Agent Work Survive the Agent.*

This candidate connects existing canonical versioned memory, tenant-scoped durable
tasks, current checkpoints and external-effect ledger through **three public
REST endpoints**. It does **not** implement a second workflow engine.

## User journey

1. Agent A writes a source-linked fact with `POST /v1/memories` and opens a task with `POST /v1/tasks`.
2. Agent A calls `POST /v1/context/prepare` supplying `task_id`, explicit
   `subject_ids` (max 20), and `token_budget` (256..8192). AMC produces a
   deterministic bounded **estimate**, including current memory IDs, versions,
   source IDs, task checkpoint and all unresolved effects. An estimated-token
   budget is **not** an LLM-tokenizer guarantee.
3. Agent A calls `POST /v1/handoffs` using the same fields plus distinct
   `source_agent` and `target_agent`. AMC persists an immutable JSON snapshot
   and SHA-256.
4. Agent B opens a *new HTTP client session* and calls
   `POST /v1/handoffs/{handoff_id}/accept`, specifying `target_agent`.
   AMC checks the tenant, target label, snapshot digest, task checkpoint,
   current claim identities, truncation and unresolved external effects.
   It returns 409 rather than claiming success if anything is stale, truncated
   or ambiguous; wrong tenant returns 404. Repeated accept by the same target
   is idempotent.

### What the handoff does **not** do

- It never starts or retries a Temporal Workflow or Provider operation.
- It never treats an external UNKNOWN result as completed.
- `source_agent` and `target_agent` are **descriptive labels**, not
  independently authenticated agent identities. Authorization presently comes
  from the tenant credential and ordinary AMC API auth. Separate per-agent
  principal and delegated-handoff policy are future security gates.
- It does not claim the receiving LLM actually understood the context or
  completed the business task. `accept_handoff` proves freshness and transfer
  validation only. A full independent Agent B continuation and business
  side-effect recovery experiment remains required for product 9.5/10.
- Context selection is canonical and deterministic, not semantic reranking.
  It refuses incomplete truncated packs at accept and does not silently
  summarize evidence away.
- Snapshots inherit the tenant's access-policy requirements. Avoid placing
  secrets or raw customer data into public CI artifacts.

## Example SDK

```python
# Two independent sessions connected to the same authenticated AMC tenant:
with MemoryClient(base_url, tenant_api_key, tenant_id=tenant) as agent_a:
    context = agent_a.prepare_context(task_id=task_id,
        subject_ids=["supplier-a"], token_budget=4096)
    handoff = agent_a.create_handoff(task_id=task_id,
        source_agent="researcher", target_agent="reviewer",
        subject_ids=["supplier-a"], token_budget=4096)

with MemoryClient(base_url, tenant_api_key, tenant_id=tenant) as agent_b:
    accepted = agent_b.accept_handoff(handoff["handoff_id"],
                                     target_agent="reviewer")
    assert accepted["status"] == "accepted"
```

## Release gate

The real-service CI test `tests/test_work_continuity.py` uses two separate
HTTPX clients, tenant-isolated FastAPI gateways and real PostgreSQL. It checks
canonical version linkage, wrong-target rejection, stale fact revision rejection,
cross-tenant isolation and idempotent acceptance.

Before calling this the completed US-C01 product: prove new *independent*
Agent B continuation using only public APIs; compare correctness, latency and
context size against full-history replay and Mem0; verify checkpoint mutation,
effect UNKNOWN and concurrent acceptance stress; and close the independent
clean-room gate in issue #57. Neither green CI nor README claims 9.5/10.
