"""US-C01 public-SDK smoke: Agent A exits; Agent B continues in a new client.

Run against the local main/PR instance after installing -e '.[api,postgres]'.
Required: MEMORY_API_KEY, MEMORY_TENANT_ID. No direct database access.
"""
from datetime import datetime, timezone
import os
from uuid import uuid4

from agent_memory_core.sdk import MemoryClient

url = os.getenv("MEMORY_API_URL", "http://127.0.0.1:8765")
key = os.environ["MEMORY_API_KEY"]
tenant = os.environ["MEMORY_TENANT_ID"]
subject = "handoff-demo-" + uuid4().hex
now = datetime.now(timezone.utc).isoformat()

with MemoryClient(url, key, tenant_id=tenant) as agent_a:
    task = agent_a.create_task(
        task_type="example.memory.batch.organize",
        input={"goal": "verify supplier terms and prepare a handoff"},
    )
    claim = agent_a.remember(
        subject_id=subject, predicate="payment_period",
        value={"period": "monthly"}, source_id="sample-contract-v1",
        valid_from=now,
    )
    package = agent_a.prepare_context(
        task_id=task["task_id"], subject_ids=[subject], token_budget=4096,
    )
    assert any(c["id"] == claim["id"] for c in package["claims"])
    handoff = agent_a.create_handoff(
        task_id=task["task_id"], source_agent="agent-A",
        target_agent="agent-B", subject_ids=[subject], token_budget=4096,
    )

# Context from agent_a is not passed into the new process/client.
with MemoryClient(url, key, tenant_id=tenant) as agent_b:
    accepted = agent_b.accept_handoff(
        handoff["handoff_id"], target_agent="agent-B",
    )
    assert accepted["status"] == "accepted"
    assert any(c["id"] == claim["id"] for c in accepted["payload"]["claims"])
    result = agent_b.remember(
        subject_id=subject, predicate="review_note",
        value={"reviewed": True, "handoff": handoff["handoff_id"]},
        source_id="work-handoff:" + handoff["handoff_id"],
        valid_from=datetime.now(timezone.utc).isoformat(),
    )
    remembered = agent_b.recall(subject)
    assert any(c["id"] == result["id"] for c in remembered["items"])
    print("US_C01_SDK_HANDOFF_ACCEPTED")
