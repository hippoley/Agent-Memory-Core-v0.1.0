"""Real PostgreSQL + real TCP API + separate Agent A/B and provider processes.

No ASGI mock transport, and no shared Python session objects. CI opts in by
setting MEMORY_TEST_DSN. A real LLM evaluation is a separate release gate.
"""
import json
import os
import signal
import secrets
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import urlopen
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "benchmarks" / "handoff" / "isolated_run.py"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ready(port):
    for _ in range(120):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.15):
                return
        except OSError:
            time.sleep(.1)
    pytest.fail(f"service not ready on port {port}")


def invoke(*args, env):
    result = subprocess.run([sys.executable, str(SCRIPT), *args],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=35, check=False)
    assert result.returncode == 0, (
        f"Agent subprocess failed at {args[0]} (exit={result.returncode})\\n"
        f"stdout={result.stdout[-4000:]}\\nstderr={result.stderr[-8000:]}"
    )
    return result


@pytest.mark.integration
@pytest.mark.parametrize("kill_after_provider_commit", [False, True], ids=["clean-restart", "sigkill-after-provider-commit"])
def test_real_api_provider_and_agent_process_restart(tmp_path, kill_after_provider_commit):
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("requires real PostgreSQL and MEMORY_TEST_DSN")
    api_port, provider_port = free_port(), free_port()
    tenant = "handoff-process-" + uuid4().hex
    subject = "supplier-process-" + uuid4().hex
    key = "secret-" + uuid4().hex + uuid4().hex
    env = dict(os.environ, MEMORY_API_KEY=key, MEMORY_TENANT_ID=tenant,
               MEMORY_API_URL=f"http://127.0.0.1:{api_port}")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # Never inherit a Provider executor secret into the Agent A/B environment.
    env.pop("PROVIDER_EFFECT_TOKEN", None)
    db_path = str(tmp_path / "provider.sqlite")
    gate = str(tmp_path / "provider-committed")
    provider_token = secrets.token_urlsafe(32)
    provider_env = {**env, "PROVIDER_EFFECT_TOKEN": provider_token,
                    "PROVIDER_REQUIRE_AMC_FENCE": "1"}
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "benchmarks.handoff.api_fixture:app",
         "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    provider = None
    try:
        ready(api_port)
        provider_args = [
            sys.executable, str(SCRIPT), "provider", "--port", str(provider_port),
            "--db", db_path,
        ]
        if kill_after_provider_commit:
            provider_args.extend(["--after-commit-gate", gate])
        provider = subprocess.Popen(
            provider_args,
            cwd=ROOT, env=provider_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        ready(provider_port)
        a = invoke("agent-a", "--subject-id", subject, env=env)
        manifest = json.loads(a.stdout.strip())
        assert manifest["subject_id"] == subject
        hid = manifest["handoff_id"]
        b_args = ("agent-b", "--handoff-id", hid, "--subject-id", subject,
                  "--provider-url", f"http://127.0.0.1:{provider_port}")
        # First accept the immutable handoff, without dispatching an effect.
        accepted = invoke(*b_args, "--accept-only", env=env)
        assert "AGENT_B_ACCEPTED" in accepted.stdout
        # Before any fenced intent exists, B must refuse Provider dispatch.
        denied = subprocess.run(
            [sys.executable, str(SCRIPT), *b_args], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=35,
        )
        assert denied.returncode != 0
        assert "AMC_EFFECT_INTENT_MISSING_OR_CONFLICT" in denied.stderr
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM effect_events").fetchone()[0] == 0
        # A separate process with PostgreSQL Worker authority claims a fenced
        # lease and transactionally registers checkpoint + effect + outbox.
        worker = subprocess.run(
            [sys.executable, str(ROOT / "benchmarks" / "handoff" / "fenced_worker.py"),
             "--task-id", manifest["task_id"],
             "--handoff-id", hid, "--subject-id", subject, "--period", "quarterly"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=35,
        )
        assert worker.returncode == 0, worker.stderr
        worker_result = json.loads(worker.stdout)
        assert worker_result["checkpoint_version"] == 1
        assert worker_result["effect_attempt_count"] == 1
        # Agent B has NO provider credential and cannot issue a POST even
        # when it has seen the signed-off AMC checkpoint.
        from urllib.error import HTTPError
        from urllib.request import Request
        forged = Request(f"http://127.0.0.1:{provider_port}/effects",
                         data=json.dumps(worker_result["effect_request"]).encode(),
                         headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(HTTPError) as forbidden:
            urlopen(forged, timeout=3)
        assert forbidden.value.code == 403
        no_receipt = subprocess.run(
            [sys.executable, str(SCRIPT), *b_args],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=35,
        )
        assert no_receipt.returncode != 0
        assert "AMC_EFFECT_NOT_COMMITTED" in no_receipt.stderr
        dispatch_args = [
            sys.executable, str(ROOT / "benchmarks" / "handoff" / "dispatch_worker.py"),
            "--task-id", manifest["task_id"],
            "--effect-key", "supplier-approval-request",
            "--worker-id", worker_result["worker_id"],
            "--lease-epoch", str(worker_result["lease_epoch"]),
            "--provider-url", f"http://127.0.0.1:{provider_port}",
        ]
        # A stale/forged Worker epoch must never get an HTTP dispatch.
        stale = dispatch_args.copy()
        stale[stale.index("--lease-epoch") + 1] = str(worker_result["lease_epoch"] - 1)
        rejected_worker = subprocess.run(
            stale, cwd=ROOT, env=provider_env,
            capture_output=True, text=True, timeout=35,
        )
        assert rejected_worker.returncode != 0
        assert "LeaseConflict" in rejected_worker.stderr
        # A valid worker ID from another tenant is equally powerless.
        other_tenant = {**provider_env, "MEMORY_TENANT_ID": tenant + "-forged"}
        rejected_tenant = subprocess.run(
            dispatch_args, cwd=ROOT, env=other_tenant,
            capture_output=True, text=True, timeout=35,
        )
        assert rejected_tenant.returncode != 0
        assert "LeaseConflict" in rejected_tenant.stderr
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM effect_events").fetchone()[0] == 0
        if kill_after_provider_commit:
            # The provider writes the marker only AFTER it has committed the
            # real SQLite effect and BEFORE it sends the HTTP response.
            first = subprocess.Popen(
                dispatch_args, cwd=ROOT, env=provider_env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 20
            try:
                while not Path(gate).exists():
                    assert first.poll() is None, "Fenced Worker exited before provider commit"
                    assert time.monotonic() < deadline, "provider never reached commit gate"
                    time.sleep(0.02)
                assert Path(gate).read_text().startswith(hid + ":")
                with sqlite3.connect(db_path) as conn:
                    committed = conn.execute(
                        "SELECT COUNT(*) FROM effect_events WHERE executed=1").fetchone()[0]
                assert committed == 1
                first.kill()  # actual SIGKILL of the Provider executor
                first.communicate(timeout=8)
                assert first.returncode == -signal.SIGKILL
            finally:
                if first.poll() is None:
                    first.kill()
                    first.communicate(timeout=8)
                Path(gate + ".release").touch()
        else:
            first = subprocess.run(
                dispatch_args, cwd=ROOT, env=provider_env,
                capture_output=True, text=True, timeout=35,
            )
            assert first.returncode == 0, first.stderr
            assert "FENCED_PROVIDER_DISPATCH_FINISHED" in first.stdout
        # Recovery runs in a new fenced process against the real AMC effect
        # state machine: EXECUTING -> UNKNOWN -> RECONCILING -> COMMITTED.
        # The old worker has exited; this test injects its lease expiry.
        recovery = subprocess.run(
            [sys.executable, str(ROOT / "benchmarks" / "handoff" / "recover_worker.py"),
             "--task-id", manifest["task_id"],
             "--effect-key", "supplier-approval-request",
             "--old-worker-id", worker_result["worker_id"],
             "--old-epoch", str(worker_result["lease_epoch"]),
             "--provider-url", f"http://127.0.0.1:{provider_port}"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=35,
        )
        assert recovery.returncode == 0, recovery.stderr
        recovered = json.loads(recovery.stdout)
        assert recovered["classification"] == "COMMITTED"
        assert recovered["reexecuted"] is False
        assert recovered["lease_epoch"] > worker_result["lease_epoch"]
        # Provider-side protection is mandatory even against a former Worker
        # that still knows the shared executor bearer token. The old epoch
        # is already fenced, and a request cannot reach SQLite effect commit.
        stale_headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + provider_token,
            "X-AMC-Tenant": tenant,
            "X-AMC-Task": manifest["task_id"],
            "X-AMC-Worker": worker_result["worker_id"],
            "X-AMC-Epoch": str(worker_result["lease_epoch"]),
        }
        stale_post = Request(
            f"http://127.0.0.1:{provider_port}/effects",
            data=json.dumps(worker_result["effect_request"]).encode(),
            headers=stale_headers, method="POST",
        )
        with pytest.raises(HTTPError) as stale_remote:
            urlopen(stale_post, timeout=3)
        assert stale_remote.value.code == 403
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM effect_events").fetchone()[0] == 1
        # Agent B independently reads the Provider receipt only AFTER the
        # fenced recovery worker has committed the corresponding AMC effect.
        # Two independent B instances race on the SAME accepted handoff.
        # Neither holds the Provider secret; both must converge on one
        # canonical idempotent AMC decision without another remote effect.
        b1 = subprocess.Popen(
            [sys.executable, str(SCRIPT), *b_args],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        b2 = subprocess.Popen(
            [sys.executable, str(SCRIPT), *b_args],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        for competing in (b1, b2):
            out, err = competing.communicate(timeout=35)
            assert competing.returncode == 0, err
            assert "AGENT_B_FINISHED" in out
        with sqlite3.connect(db_path) as conn:
            events = conn.execute(
                "SELECT request_id, effect_key, executed FROM effect_events ORDER BY id"
            ).fetchall()
        # Agent B cannot send POST: there was only one authorized execution.
        assert len(events) == 1
        assert [e[2] for e in events] == [1]
        assert len({e[0] for e in events}) == 1
        from agent_memory_core.sdk import MemoryClient
        with MemoryClient(env["MEMORY_API_URL"], key, tenant_id=tenant) as reader:
            claims = reader.recall(subject)["items"]
        decisions = [c for c in claims if c["predicate"] == "handoff_decision"]
        assert len(decisions) == 1
        assert decisions[0]["value"] == {
            "payment_period": "quarterly", "requires_approval": True,
        }
        # Genuine AMC intent must have been registered by the separately
        # fenced Worker BEFORE the provider call (not fabricated afterward).
        from agent_memory_core.postgres import PostgresMemory
        from benchmarks.handoff.effect_reconciliation import (
            assess_effect, load_tenant_effect,
        )
        db = PostgresMemory(env["MEMORY_TEST_DSN"])
        try:
            recorded = load_tenant_effect(
                db.conn, tenant_id=tenant, task_id=manifest["task_id"],
                effect_key="supplier-approval-request")
        finally:
            db.close()
        assert recorded is not None
        assert recorded["status"] == "committed"
        result = assess_effect(
            recorded=recorded,
            request_id=hid + ":supplier-approval-request",
            effect_key="supplier-approval-request",
            expected_payload={"subject": subject, "period": "quarterly"},
            receipt={"status": "committed",
                     "request_id": hid + ":supplier-approval-request",
                     "effect_key": "supplier-approval-request",
                     "payload": {"subject": subject, "period": "quarterly"}},
        )
        # Cross-ledger comparison alone is never authorization. Only the
        # independently fenced Worker above was permitted to resolve UNKNOWN.
        assert result.classification == "RECEIPTS_CONSISTENT_READ_ONLY"
        assert result.matches_recorded_intent is True
        assert result.safe_for_automatic_completion is False
    finally:
        for proc in (provider, api):
            if proc is not None:
                proc.terminate()
                try:
                    proc.communicate(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate(timeout=8)
