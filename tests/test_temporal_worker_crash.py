"""Kill a live Temporal Worker process and prove retry on a replacement Worker."""
import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_sigkill_worker_activity_retries_on_replacement_process(tmp_path):
    if os.getenv("MEMORY_TEMPORAL_CRASH_TEST") != "1":
        pytest.skip("Set MEMORY_TEMPORAL_CRASH_TEST=1 in the dedicated Temporal CI job")
    pytest.importorskip("temporalio")
    from temporalio.testing import WorkflowEnvironment

    fixture_path = Path(__file__).with_name("temporal_crash_fixture.py")
    spec = importlib.util.spec_from_file_location("temporal_crash_fixture", fixture_path)
    fixture = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(fixture)

    async def scenario():
        env = await WorkflowEnvironment.start_local()
        queue = "crash-recovery-" + uuid4().hex
        workflow_id = "crash-recovery-" + uuid4().hex
        marker = tmp_path / "worker-a-started"
        address = env.client.service_client.config.target_host
        worker_a = worker_b = None
        try:
            handle = await env.client.start_workflow(
                fixture.CrashRecoveryWorkflow.run,
                {"marker": str(marker)},
                id=workflow_id,
                task_queue=queue,
            )
            worker_a = subprocess.Popen(
                [sys.executable, str(fixture_path), address, queue],
                cwd=str(fixture_path.parents[1]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not marker.exists():
                if worker_a.poll() is not None:
                    raise AssertionError("Worker A exited before starting its Activity")
                await asyncio.sleep(0.05)
            assert marker.exists(), "Worker A did not start the long-running Activity"
            worker_a.kill()
            await asyncio.to_thread(worker_a.wait, 10)

            worker_b = subprocess.Popen(
                [sys.executable, str(fixture_path), address, queue],
                cwd=str(fixture_path.parents[1]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            result = await asyncio.wait_for(handle.result(), timeout=40)
            assert result == {"attempt": 2, "recovered": True}
            assert marker.read_text(encoding="utf-8") == "worker-b-retried"
        finally:
            for process in (worker_a, worker_b):
                if process is not None and process.poll() is None:
                    process.kill()
                    await asyncio.to_thread(process.wait, 10)
            await env.shutdown()

    asyncio.run(scenario())
