"""Real OS-process crash/reclaim/resume acceptance for DurableTaskWorker."""
import os
import signal
import subprocess
import sys
import time
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL acceptance requires POSIX")
def test_sigkill_worker_a_and_resume_checkpoint_in_worker_b(tmp_path):
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    pytest.importorskip("psycopg")
    from agent_memory_core.durable_tasks import DurableTaskStore
    from agent_memory_core.postgres import PostgresMemory

    tenant="sigkill-runtime-"+uuid4().hex
    marker=tmp_path/"worker-a-checkpointed"
    db=PostgresMemory(dsn)
    try:
        task=DurableTaskStore(db.conn).create_task(
            tenant_id=tenant,task_type="test.sigkill.resume",
            input={"expected_cursor":1},idempotency_key="sigkill-"+uuid4().hex)
        task_id=task["task_id"]
    finally:
        db.close()

    common = """
import os,time
from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.worker_runtime import DurableTaskWorker
def connect():
    return PostgresMemory(os.environ["MEMORY_TEST_DSN"])
"""
    worker_a=common+"""
def handler(task,ctx):
    ctx.commit_checkpoint(checkpoint={"cursor":1},
        idempotency_key="sigkill-checkpoint-1",
        progress_completed=1,progress_total=2)
    open(os.environ["WORKER_MARKER"],"w").write(task["task_id"])
    time.sleep(90)
worker=DurableTaskWorker(connection_factory=connect,tenant_id=os.environ["WORKER_TENANT"],
    handlers={"test.sigkill.resume":handler},worker_id="worker-A",lease_seconds=2,
    heartbeat_interval=0.5)
worker.run_once()
"""
    env=os.environ.copy()
    env.update({"WORKER_TENANT":tenant,"WORKER_MARKER":str(marker)})
    proc=subprocess.Popen([sys.executable,"-c",worker_a],env=env)
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and not marker.exists():
            if proc.poll() is not None:
                raise AssertionError("Worker A exited before committing its checkpoint")
            time.sleep(0.1)
        assert marker.exists(), "Worker A did not reach the post-checkpoint crash point"
        os.kill(proc.pid,signal.SIGKILL)
        proc.wait(timeout=10)
        time.sleep(2.5)  # PostgreSQL lease expiry; no worker clock participates in claim.

        worker_b=common+"""
def handler(task,ctx):
    checkpoint=task.get("checkpoint") or {}
    assert checkpoint.get("cursor")==1, checkpoint
    return {"resumed_from":checkpoint["cursor"],"worker":"B"}
worker=DurableTaskWorker(connection_factory=connect,tenant_id=os.environ["WORKER_TENANT"],
    handlers={"test.sigkill.resume":handler},worker_id="worker-B",lease_seconds=5,
    heartbeat_interval=1)
assert worker.run_once() is True
"""
        completed=subprocess.run([sys.executable,"-c",worker_b],env=env,
                                 capture_output=True,text=True,timeout=30)
        assert completed.returncode==0,completed.stdout+"\\n"+completed.stderr
        check=PostgresMemory(dsn)
        try:
            final=DurableTaskStore(check.conn).get_task(tenant_id=tenant,task_id=task_id)
            assert final["status"]=="completed",final
            assert final["checkpoint"]["checkpoint"]["cursor"]==1
            assert final["result"]=={"resumed_from":1,"worker":"B"}
        finally:
            check.close()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
