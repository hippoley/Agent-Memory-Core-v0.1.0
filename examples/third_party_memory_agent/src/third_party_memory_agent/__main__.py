"""Run a complete memory lifecycle using only the installed public SDK.

Required environment:
  MEMORY_BASE_URL, MEMORY_API_KEY, MEMORY_TENANT_ID

Optional:
  MEMORY_RUN_TEMPORAL=1 (requires the Temporal capability and worker)
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from agent_memory_core.sdk import MemoryClient


def main() -> None:
    base_url = os.environ["MEMORY_BASE_URL"]
    api_key = os.environ["MEMORY_API_KEY"]
    tenant_id = os.environ["MEMORY_TENANT_ID"]
    suffix = uuid.uuid4().hex
    subject = "third-party-agent-" + suffix
    now = datetime.now(timezone.utc)
    earlier = now.isoformat()
    later = (now + timedelta(seconds=1)).isoformat()

    with MemoryClient(base_url, api_key, tenant_id=tenant_id) as memory:
        ready = memory.ready()
        identity = memory.whoami()
        assert identity["tenant_id"] == tenant_id, identity
        capabilities = memory.capabilities()
        enabled = {item["name"]: item["enabled"]
                   for item in capabilities.get("capabilities", [])}

        first = memory.remember(
            subject_id=subject,
            predicate="preferred_language",
            value={"language": "English"},
            source_id="third-party-agent:" + suffix + ":source-1",
            valid_from=earlier,
            idempotency_key="third-party-agent:" + suffix + ":write-1",
        )
        first_id = first["id"]

        second = memory.remember(
            subject_id=subject,
            predicate="preferred_language",
            value={"language": "Chinese"},
            source_id="third-party-agent:" + suffix + ":source-2",
            valid_from=later,
            replaces_id=first_id,
            idempotency_key="third-party-agent:" + suffix + ":write-2",
        )
        second_id = second["id"]
        assert second_id != first_id

        current = memory.recall(subject, predicate="preferred_language")
        history = memory.list_memories(
            subject_id=subject,
            predicate="preferred_language",
            include_superseded=True,
        )
        assert len(current["items"]) == 1, current
        assert current["items"][0]["value"]["language"] == "Chinese", current
        assert len(history["items"]) == 2, history

        search = memory.search(query="preferred language Chinese", limit=10)
        assert any(
            item.get("memory_id") == second_id
            for item in search.get("items", [])
        ), search

        sources = memory.sources(second_id)
        assert sources.get("sources"), sources
        events = memory.events(limit=100)
        assert events.get("items") is not None, events

        workflow = None
        if os.getenv("MEMORY_RUN_TEMPORAL") == "1":
            workflow = memory.verify_memory_async(second_id)
            status = memory.workflow_status(workflow["workflow_id"])
            assert status["status"] in {"STARTED", "RUNNING", "COMPLETED"}, status

        forgotten = memory.forget(second_id)
        assert forgotten.get("forgotten") == [second_id], forgotten
        after_forget = memory.recall(subject, predicate="preferred_language")
        assert after_forget["items"] == [], after_forget
        retained = memory.list_memories(
            subject_id=subject,
            predicate="preferred_language",
            include_superseded=True,
        )
        assert len(retained["items"]) == 2, retained

        result = {
            "ready": ready,
            "tenant_id": tenant_id,
            "identity": identity,
            "capabilities": capabilities,
            "created_ids": [first_id, second_id],
            "current_before_revoke": current["items"][0]["value"],
            "history_versions": len(history["items"]),
            "search_matches": len(search.get("items", [])),
            "provenance_sources": len(sources["sources"]),
            "events_read": len(events["items"]),
            "evidence_object_sha256": object_digest,
            "temporal_workflow": workflow,
            "history_after_revoke": len(retained["items"]),
            "status": "THIRD_PARTY_SDK_ACCEPTED",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
