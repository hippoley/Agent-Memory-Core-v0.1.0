"""Process-level SIGKILL acceptance for the PostgreSQL -> Temporal Dispatcher.

Requires MEMORY_TEST_DSN and a real Temporal service at MEMORY_TEMPORAL_ADDRESS.
The test kills an independent dispatcher process at deterministic barriers,
then starts a replacement and independently reads PostgreSQL + Temporal.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def _wait_for(path, process, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return
        if process.poll() is not None:
            raise AssertionError(f"Dispatcher exited before barrier: {process.returncode}")
        time.sleep(0.02)
    raise TimeoutError(f"Dispatcher did not reach barrier: {path}")


def _wait_for_lease_expiry(db, task_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = db.conn.execute(
            """SELECT lease_expires_at <= clock_timestamp()
               FROM memory_temporal_dispatches WHERE task_id=%s::uuid""",
            (task_id,),
        ).fetchone()
        if row and row[0]:
            return
        time.sleep(0.05)
    raise TimeoutError("Dispatcher lease did not expire")


@pytest.mark.parametrize(
    ("failpoint", "expect_existing_workflow"),
    [
        ("before_claim_commit", False),
        ("after_claim_commit", False),
        ("after_temporal_start_before_ack", True),
    ],
)
def test_dispatcher_sigkill_recovers_both_external_side_effect_windows(
    tmp_path, monkeypatch, failpoint, expect_existing_workflow
):
    dsn = os.getenv("MEMORY_TEST_DSN")
    address = os.getenv("MEMORY_TEMPORAL_ADDRESS")
    if not dsn or not address:
        pytest.skip("MEMORY_TEST_DSN and MEMORY_TEMPORAL_ADDRESS are required")
    pytest.importorskip("temporalio")

    import asyncio
    from temporalio.client import Client
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.durable_tasks import DurableTaskStore

    queue = "dispatcher-sigkill-" + uuid4().hex
    tenant = "dispatcher-sigkill-" + uuid4().hex
    monkeypatch.setenv("MEMORY_DSN", dsn)
    monkeypatch.setenv("MEMORY_TEST_DSN", dsn)
    monkeypatch.setenv("MEMORY_TEMPORAL_ADDRESS", address)
    monkeypatch.setenv("MEMORY_TEMPORAL_TASK_QUEUE", queue)
    monkeypatch.setenv("MEMORY_TEMPORAL_DISPATCH_LEASE_SECONDS", "1")
    monkeypatch.setenv("MEMORY_TEMPORAL_DISPATCH_ONCE", "1")
    monkeypatch.setenv("MEMORY_TEMPORAL_REQUIRE_RLS", "0")

    db = PostgresMemory(dsn)
    process = None
    try:
        db.migrate()
        task = DurableTaskStore(db.conn).create_task(
            tenant_id=tenant,
            task_type="example.memory.batch.organize",
            input={"items": [{"id": "sigkill"}]},
        )
        task_id = task["task_id"]
        workflow_id = task["workflow_id"]
        barrier_dir = str(tmp_path)

        env = os.environ.copy()
        env.update({
            "MEMORY_DSN": dsn,
            "MEMORY_TEST_DSN": dsn,
            "MEMORY_TEMPORAL_ADDRESS": address,
            "MEMORY_TEMPORAL_TASK_QUEUE": queue,
            "MEMORY_TEMPORAL_DISPATCH_LEASE_SECONDS": "1",
            "MEMORY_TEMPORAL_DISPATCH_ONCE": "1",
            "MEMORY_TEMPORAL_DISPATCHER_ID": "sigkill-a-" + uuid4().hex,
            "MEMORY_TEMPORAL_DISPATCH_TEST_MODE": "1",
            "MEMORY_TEMPORAL_DISPATCH_TEST_FAILPOINT": failpoint,
            "MEMORY_TEMPORAL_DISPATCH_TEST_DIR": barrier_dir,
            "MEMORY_TEMPORAL_REQUIRE_RLS": "0",
        })
        process = subprocess.Popen(
            [sys.executable, "-m", "agent_memory_core.temporal_dispatcher"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for(os.path.join(barrier_dir, failpoint + ".reached"), process)
        barrier_at = datetime.now(timezone.utc).isoformat()

        before = db.conn.execute(
            """SELECT status,lease_owner,lease_epoch,attempts,temporal_run_id
               FROM memory_temporal_dispatches WHERE task_id=%s::uuid""",
            (task_id,),
        ).fetchone()
        if failpoint == "before_claim_commit":
            # Uncommitted UPDATE is invisible to this independent connection.
            assert before[0] == "pending"
            assert before[1] is None
            assert before[2] == 0
            assert before[3] == 0
            assert before[4] is None
        else:
            assert before[0] == "dispatching"
            assert before[2] == 1
            assert before[4] is None

        original_run_id = None
        if expect_existing_workflow:
            async def read_original():
                client = await Client.connect(address)
                handle = client.get_workflow_handle(workflow_id)
                description = await handle.describe()
                return description.run_id
            original_run_id = asyncio.run(read_original())
            assert original_run_id

        process.send_signal(signal.SIGKILL)
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == -signal.SIGKILL, stderr.decode(errors="replace")
        killed_at = datetime.now(timezone.utc).isoformat()

        if failpoint != "before_claim_commit":
            _wait_for_lease_expiry(db, task_id)
        recovery_env = env.copy()
        recovery_env["MEMORY_TEMPORAL_DISPATCHER_ID"] = "sigkill-b-" + uuid4().hex
        recovery_env.pop("MEMORY_TEMPORAL_DISPATCH_TEST_FAILPOINT", None)
        recovery_env.pop("MEMORY_TEMPORAL_DISPATCH_TEST_DIR", None)
        recovery_env["MEMORY_TEMPORAL_DISPATCH_TEST_MODE"] = "0"
        recovered = subprocess.run(
            [sys.executable, "-m", "agent_memory_core.temporal_dispatcher"],
            env=recovery_env, capture_output=True, text=True, timeout=45,
        )
        assert recovered.returncode == 0, recovered.stderr
        recovery_at = datetime.now(timezone.utc).isoformat()

        after = db.conn.execute(
            """SELECT status,lease_owner,lease_epoch,attempts,temporal_run_id
               FROM memory_temporal_dispatches WHERE task_id=%s::uuid""",
            (task_id,),
        ).fetchone()
        assert after[0] == "started"
        expected_epoch = 1 if failpoint == "before_claim_commit" else 2
        assert after[2] == expected_epoch
        assert after[3] == expected_epoch
        assert after[4]
        if original_run_id:
            assert after[4] == original_run_id

        async def verify_temporal():
            client = await Client.connect(address)
            handle = client.get_workflow_handle(workflow_id, run_id=after[4])
            description = await handle.describe()
            assert description.run_id == after[4]
            assert description.id == workflow_id
            assert description.task_queue == queue

            # Read the actual Temporal event history independently of the
            # PostgreSQL dispatch ledger. Never convert failed reads into PASS.
            events = []
            async for event in handle.fetch_history_events():
                event_type = getattr(event.event_type, "name", str(event.event_type))
                events.append({"event_id": event.event_id, "event_type": event_type})
            assert events, "Temporal history was empty"
            assert events[0]["event_type"] == "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"
            assert len({event["event_id"] for event in events}) == len(events)
            assert all(event["event_id"] > 0 for event in events)
            return description.run_id, events

        verified_run_id, temporal_events = asyncio.run(verify_temporal())
        assert verified_run_id == after[4]

        # Write receipts only after all assertions have passed. No DSNs or secrets.
        evidence_dir = os.getenv("MEMORY_DA_EVIDENCE_DIR")
        if evidence_dir:
            evidence = {
                "schema": "amc.dispatcher-sigkill.v1",
                "scenario": failpoint,
                "task_id": str(task_id),
                "workflow_id": workflow_id,
                "timestamps_utc": {
                    "barrier_reached": barrier_at,
                    "dispatcher_killed": killed_at,
                    "recovery_completed": recovery_at,
                },
                "process": {
                    "kill_signal": "SIGKILL",
                    "dispatcher_exit_code": process.returncode,
                    "replacement_exit_code": recovered.returncode,
                },
                "postgresql": {
                    "before": {
                        "status": before[0],
                        "lease_owner": before[1],
                        "lease_epoch": before[2],
                        "attempts": before[3],
                        "temporal_run_id": before[4],
                    },
                    "after": {
                        "status": after[0],
                        "lease_owner": after[1],
                        "lease_epoch": after[2],
                        "attempts": after[3],
                        "temporal_run_id": after[4],
                    },
                },
                "temporal": {
                    "described_workflow_id": workflow_id,
                    "described_run_id": verified_run_id,
                    "pre_kill_run_id": original_run_id,
                    "task_queue": queue,
                    "history_exported": True,
                    "history_event_count": len(temporal_events),
                    "history_events": temporal_events,
                },
                "assertions": {
                    "sigkill_exit_verified": True,
                    "replacement_success": True,
                    "expected_fencing_epoch": expected_epoch,
                    "postgres_started": True,
                    "temporal_run_id_matches": True,
                    "temporal_history_nonempty_and_started": True,
                    "original_run_id_preserved_if_preexisting": True,
                },
                "result": "PASS",
            }
            target = Path(evidence_dir)
            target.mkdir(parents=True, exist_ok=True)
            filename = target / f"{failpoint}.json"
            filename.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        db.close()
