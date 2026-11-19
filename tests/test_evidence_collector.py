"""Tests for read-only live evidence collection adapters."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio

from agent_memory_core.evidence_collector import collect_live_temporal_dispatch_evidence
from agent_memory_core.durable_evidence import adjudicate_evidence


NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
TENANT = "tenant-collector"
TASK = "00000000-0000-0000-0000-000000000001"
WORKFLOW = "workflow-collector"


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))
        assert params == (TENANT, 10)
        return FakeRows([self.row])


class FakeHandle:
    async def describe(self):
        async def memo():
            return {
                "agent_memory_task_identity": {
                    "tenant_id": TENANT,
                    "task_id": TASK,
                    "workflow_id": WORKFLOW,
                    "task_type": "test.task",
                }
            }
        return SimpleNamespace(
            id=WORKFLOW,
            run_id="run-collector",
            status="RUNNING",
            workflow_type="DurableTaskWorkflow",
            task_queue="queue-collector",
            memo=memo,
        )


class FakeTemporalClient:
    def get_workflow_handle(self, workflow_id):
        assert workflow_id == WORKFLOW
        return FakeHandle()


def make_row(*, task_workflow_id=WORKFLOW, effects=None):
    return (
        TASK, TENANT, "test.task", task_workflow_id, WORKFLOW, "running",
        "worker-a", 4, NOW + timedelta(minutes=1), NOW, True,
        "dispatching", "queue-collector", None, "dispatcher-a", 2,
        NOW + timedelta(minutes=1), 1, NOW,
        effects or [{
            "effect_key": "device-write",
            "status": "unknown",
            "request_sha256": "a" * 64,
            "attempt_count": 1,
            "execution_worker_id": None,
            "execution_lease_epoch": None,
            "provider_reference": None,
            "provider_result_present": False,
        }],
    )


def test_live_collector_emits_scoped_pg_temporal_and_effect_observations():
    conn = FakeConnection(make_row())
    evidence = asyncio.run(collect_live_temporal_dispatch_evidence(
        conn, FakeTemporalClient(), tenant_id=TENANT, limit=10,
    ))

    sources = [item.source for item in evidence]
    assert sources.count("postgres_task") == 1
    assert sources.count("postgres_dispatch") == 1
    assert sources.count("postgres_worker_lease") == 1
    assert sources.count("effect_ledger") == 1
    assert sources.count("temporal_lookup") == 1
    assert "temporal_history" not in sources
    assert "provider_receipt" not in sources
    assert all(item.tenant_id == TENANT and item.task_id == TASK for item in evidence)
    assert all(item.workflow_id == WORKFLOW for item in evidence)

    temporal = next(item for item in evidence if item.source == "temporal_lookup")
    assert temporal.payload["lookup_status"] == "FOUND"
    assert temporal.payload["workflow_type_matches"] is True
    assert temporal.run_id == "run-collector"

    effect = next(item for item in evidence if item.source == "effect_ledger")
    assert effect.payload["status"] == "unknown"
    assert effect.payload["provider_receipt_available"] is False

    result = adjudicate_evidence(evidence)
    assert result.outcome == "IN_PROGRESS"
    assert result.recovery_authorization == "DENY"


def test_live_collector_preserves_task_vs_dispatch_workflow_id_mismatch():
    conn = FakeConnection(make_row(task_workflow_id="wrong-task-workflow"))
    evidence = asyncio.run(collect_live_temporal_dispatch_evidence(
        conn, FakeTemporalClient(), tenant_id=TENANT, limit=10,
    ))
    task = next(item for item in evidence if item.source == "postgres_task")
    assert task.payload["dispatch_workflow_id_matches"] is False
    result = adjudicate_evidence(evidence)
    assert result.outcome == "CONFLICT"
    assert result.recovery_authorization == "DENY"


def test_live_collector_does_not_treat_temporal_transport_error_as_not_found():
    class UnavailableClient:
        def get_workflow_handle(self, workflow_id):
            class Handle:
                async def describe(self):
                    class RpcError(Exception):
                        status = SimpleNamespace(name="UNAVAILABLE")
                    raise RpcError("connection refused")
            return Handle()

    conn = FakeConnection(make_row(effects=[]))
    evidence = asyncio.run(collect_live_temporal_dispatch_evidence(
        conn, UnavailableClient(), tenant_id=TENANT, limit=10,
    ))
    temporal = next(item for item in evidence if item.source == "temporal_lookup")
    assert temporal.payload["lookup_status"] == "ERROR"
    result = adjudicate_evidence(evidence)
    assert result.outcome == "UNKNOWN"
    assert result.recovery_authorization == "DENY"


def test_live_collector_rejects_missing_scope_and_invalid_limit():
    import pytest
    conn = FakeConnection(make_row())
    with pytest.raises(ValueError, match="tenant_id"):
        asyncio.run(collect_live_temporal_dispatch_evidence(
            conn, FakeTemporalClient(), tenant_id=" ", limit=10,
        ))
    with pytest.raises(ValueError, match="limit"):
        asyncio.run(collect_live_temporal_dispatch_evidence(
            conn, FakeTemporalClient(), tenant_id=TENANT, limit=0,
        ))
