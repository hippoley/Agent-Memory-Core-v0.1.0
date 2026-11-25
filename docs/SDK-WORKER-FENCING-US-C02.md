## US-C02 · Python SDK worker/fencing contract

A memory reader or Agent B **cannot** mark an accepted handoff's durable task
complete. Only a dedicated API credential with the exact `tasks:execute`
scope can claim, checkpoint and finish it under the server-issued lease epoch.

```python
# Use an independently issued, scoped worker credential, not the memory API key.
from agent_memory_core.sdk import MemoryClient

with MemoryClient(url, worker_token, tenant_id=tenant) as worker:
    claimed = worker.claim_task(worker_id="continuity-worker-1")["task"]
    # Execute only a task type supported by the worker. Claiming a task does
    # not itself authorize physical device execution.
    task_id = claimed["task_id"]
    epoch = claimed["lease_epoch"]
    worker.commit_task_checkpoint(
        task_id, worker_id="continuity-worker-1", lease_epoch=epoch,
        expected_version=claimed["checkpoint_version"],
        checkpoint={"stage": "prepared"}, idempotency_key="job:prepared")
    # Call set_task_status only after the durable external effect ledger shows
    # all required effects committed and source receipts have been reconciled.
    # worker.set_task_status(task_id, worker_id="continuity-worker-1",
    #                        lease_epoch=epoch, status="completed", result={...})
```

This SDK patch closes missing required request parameters and the claim/renew
entrypoints. It **does not** integrate a live provider into the Worker, change
task semantics, or confer independent Agent identities. The server still
enforces scope, lease epoch and unresolved-effect checks; SDK validation
alone is not a security boundary.

