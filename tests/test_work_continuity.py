"""US-C01: two independent HTTP sessions hand off a live durable task.

Real PostgreSQL is exercised in the Temporal integration CI; isolated
validation still runs with the ordinary Python test job.
"""
from datetime import datetime, timezone
from uuid import uuid4
import os

import pytest

from agent_memory_core.work_continuity import HandoffConflict, _require_ids, _tokens


def test_context_budget_and_subjects_fail_closed():
    with pytest.raises(ValueError):
        _require_ids([])
    with pytest.raises(ValueError):
        _require_ids([""])
    assert _require_ids(["x", "x", "y"]) == ["x", "y"]
    assert _tokens({"x": "hello"}) > 0


def test_public_python_sdk_routes_are_real_http_contracts():
    import httpx
    from agent_memory_core.sdk import MemoryClient

    seen = []
    def respond(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    with MemoryClient("http://amc.local", "x" * 32,
                      tenant_id="tenant-a",
                      transport=httpx.MockTransport(respond)) as client:
        assert client.prepare_context(task_id="task", subject_ids=["supplier"])["ok"]
        assert client.create_handoff(task_id="task", source_agent="A",
                                     target_agent="B", subject_ids=["supplier"])["ok"]
        assert client.accept_handoff("handoff", target_agent="B")["ok"]
    assert seen == [
        ("POST", "/v1/context/prepare"),
        ("POST", "/v1/handoffs"),
        ("POST", "/v1/handoffs/handoff/accept"),
    ]


@pytest.mark.integration
def test_us_c01_real_http_two_agent_handoff_and_adversarial_rejection():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("US-C01 requires real PostgreSQL")
    import asyncio
    import httpx
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.api import create_app

    dsn = os.environ["MEMORY_TEST_DSN"]
    db = PostgresMemory(dsn)
    db.migrate()
    db.close()
    tenant = "handoff-" + uuid4().hex
    api_key = "secret-" + uuid4().hex + uuid4().hex
    subject = "supplier-" + uuid4().hex

    app = create_app(api_key=api_key, tenant_id=tenant,
                     store_factory=lambda: PostgresMemory(dsn))
    other = create_app(api_key=api_key, tenant_id="other-"+tenant,
                       store_factory=lambda: PostgresMemory(dsn))

    async def scenario():
        # Separate HTTP clients; neither client has access to the other's
        # in-memory session or the server's PostgreSQL handle.
        headers = {"Authorization": "Bearer " + api_key}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://amc.local", headers=headers) as agent_a:
            task_response = await agent_a.post("/v1/tasks", json={
                "task_type": "example.memory.batch.organize",
                "input": {"topic": "supplier contract"},
            })
            assert task_response.status_code == 201, task_response.text
            task = task_response.json()
            task_id = task["task_id"]

            write = await agent_a.post("/v1/memories", json={
                "subject_id": subject, "predicate": "payment_period",
                "value": {"period": "monthly"}, "source_id": "contract:v1",
                "valid_from": datetime.now(timezone.utc).isoformat(),
            })
            assert write.status_code == 201, write.text
            memory_id = write.json()["id"]
            base = {"task_id": task_id, "subject_ids": [subject], "token_budget": 4096}
            prepared = await agent_a.post("/v1/context/prepare", json=base)
            assert prepared.status_code == 200, prepared.text
            assert memory_id in [c["id"] for c in prepared.json()["claims"]]
            handoff = await agent_a.post("/v1/handoffs", json={
                **base, "source_agent": "agent-A", "target_agent": "agent-B",
            })
            assert handoff.status_code == 201, handoff.text
            handoff_id = handoff.json()["handoff_id"]
            old_context = await agent_a.post("/v1/handoffs", json={
                **base, "source_agent": "agent-A", "target_agent": "agent-B",
            })
            assert old_context.status_code == 201, old_context.text
            old_context_id = old_context.json()["handoff_id"]

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://amc.local", headers=headers) as agent_b:
            wrong = await agent_b.post(f"/v1/handoffs/{handoff_id}/accept",
                                       json={"target_agent": "agent-C"})
            assert wrong.status_code == 409, wrong.text
            accepted = await agent_b.post(f"/v1/handoffs/{handoff_id}/accept",
                                          json={"target_agent": "agent-B"})
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["status"] == "accepted"
            assert accepted.json()["idempotent"] is False
            repeat_unchanged = await agent_b.post(
                f"/v1/handoffs/{handoff_id}/accept",
                json={"target_agent": "agent-B"})
            assert repeat_unchanged.status_code == 200, repeat_unchanged.text
            assert repeat_unchanged.json()["idempotent"] is True
            # Agent B performs new work through the public canonical memory API,
            # not through Agent A's process state or a privileged database handle.
            continuation = await agent_b.post("/v1/memories", json={
                "subject_id": subject, "predicate": "review_note",
                "value": {"reviewed": True, "handoff_id": handoff_id},
                "source_id": "handoff:" + handoff_id,
                "valid_from": datetime.now(timezone.utc).isoformat(),
            })
            assert continuation.status_code == 201, continuation.text
            reviewed_id = continuation.json()["id"]
            reviewed = await agent_b.get(f"/v1/memories/{subject}")
            assert reviewed.status_code == 200
            assert reviewed_id in [c["id"] for c in reviewed.json()["items"]]
            added_fact_conflict = await agent_b.post(
                f"/v1/handoffs/{old_context_id}/accept",
                json={"target_agent": "agent-B"})
            assert added_fact_conflict.status_code == 409, added_fact_conflict.text
            again = await agent_b.post(f"/v1/handoffs/{handoff_id}/accept",
                                       json={"target_agent": "agent-B"})
            assert again.status_code == 409, again.text
            assert "claims changed" in again.text

            # A second packet becomes stale after a real memory revision.
            stale = await agent_b.post("/v1/handoffs", json={
                **base, "source_agent": "agent-A", "target_agent": "agent-B",
            })
            assert stale.status_code == 201, stale.text
            new = await agent_b.post("/v1/memories", json={
                "subject_id": subject, "predicate": "payment_period",
                "value": {"period": "quarterly"}, "source_id": "contract:v2",
                "valid_from": datetime.now(timezone.utc).isoformat(),
                "replaces_id": memory_id,
            })
            assert new.status_code == 201, new.text
            stale_result = await agent_b.post(
                f"/v1/handoffs/{stale.json()['handoff_id']}/accept",
                json={"target_agent": "agent-B"})
            assert stale_result.status_code == 409, stale_result.text

            # Fault injection is performed by the test harness, not Agent B.
            # An effect goes UNKNOWN after handoff creation: accept must
            # re-read current PostgreSQL state rather than trust the snapshot.
            pending = await agent_b.post("/v1/handoffs", json={
                **base, "source_agent": "agent-A", "target_agent": "agent-B",
            })
            assert pending.status_code == 201, pending.text
            fault_db = PostgresMemory(dsn)
            try:
                with fault_db.conn.transaction():
                    fault_db.conn.execute(
                        """INSERT INTO memory_task_effects
                           (task_id,effect_key,request_sha256,status)
                           VALUES (%s::uuid,%s,%s,%s)""",
                        (task_id, "provider-side-effect", "0" * 64, "unknown"),
                    )
            finally:
                fault_db.close()
            unknown = await agent_b.post(
                f"/v1/handoffs/{pending.json()['handoff_id']}/accept",
                json={"target_agent": "agent-B"})
            assert unknown.status_code == 409, unknown.text

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other),
                                     base_url="http://amc.other", headers=headers) as other_tenant:
            cross_tenant = await other_tenant.post(
                f"/v1/handoffs/{handoff_id}/accept", json={"target_agent": "agent-B"})
            assert cross_tenant.status_code == 404

        # Final canonical state is quarterly, with the superseded claim retained.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://amc.local", headers=headers) as check:
            response = await check.get(f"/v1/memories/{subject}")
            assert response.status_code == 200
            values = [c["value"]["period"] for c in response.json()["items"]
                      if c["predicate"] == "payment_period"]
            assert values == ["quarterly"]
            assert any(c["predicate"] == "review_note" for c in response.json()["items"])

    asyncio.run(scenario())
