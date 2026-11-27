"""US-C01: source admission and durable effect-intent must share DB transaction.

These are focused in-process contract checks. They are NOT a deterministic
concurrent-revocation PostgreSQL/remote-Provider acceptance; issue #67 stays open.
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "benchmarks" / "handoff" / "fenced_worker.py"
SPEC = importlib.util.spec_from_file_location("fenced_worker_source_gate_test", MODULE)
fenced_worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fenced_worker)


class FakeDB:
    def __init__(self, events):
        self.events = events
        self.conn = self

    @contextmanager
    def transaction(self):
        self.events.append("txn_open")
        try:
            yield
        except BaseException:
            self.events.append("txn_rollback")
            raise
        else:
            self.events.append("txn_commit")

    def close(self):
        self.events.append("close")


def install_fakes(monkeypatch, events):
    db = FakeDB(events)
    monkeypatch.setenv("MEMORY_TEST_DSN", "fixture-only-not-postgres")
    monkeypatch.setattr(fenced_worker, "PostgresMemory", lambda _: db)

    class FakeLease:
        def __init__(self, conn):
            assert conn is db

        def claim(self, **kw):
            events.append("claim")
            return {"task_id": "task", "tenant_id": "tenant",
                    "lease_epoch": 1, "checkpoint_version": 0}

        def commit_checkpoint(self, **kw):
            events.append("commit_intent")
            return {"version": 1, "replayed": False}

    class FakeEffect:
        def __init__(self, conn):
            assert conn is db

        def begin_execution(self, **kw):
            events.append("begin_effect")
            return {"idempotency_key": "fixed-key", "attempt_count": 1}

    monkeypatch.setattr(fenced_worker, "DurableTaskLeaseStore", FakeLease)
    monkeypatch.setattr(fenced_worker, "DurableTaskEffectStore", FakeEffect)
    return db


def test_verified_source_is_checked_before_intent_in_same_transaction(monkeypatch):
    events = []
    db = install_fakes(monkeypatch, events)

    def check(conn):
        assert conn is db
        assert events == ["txn_open"]
        events.append("source_locked")
        return SimpleNamespace(state="VERIFIED", allowed_uses=("plan",),
                               reason="INDEPENDENT_SOURCE_MATCH")

    result = fenced_worker.register_intent(
        tenant_id="tenant", task_id="task", handoff_id="handoff",
        subject_id="supplier", period="quarterly", source_gate=check)
    assert result["checkpoint_version"] == 1
    assert events == ["txn_open", "source_locked", "claim",
                      "commit_intent", "begin_effect", "txn_commit", "close"]


@pytest.mark.parametrize("state,allowed,reason", [
    ("DENIED", (), "SOURCE_AUTHORITY_NOT_ACTIVE"),
    ("STALE", (), "SOURCE_REVISION_CHANGED"),
    ("VERIFIED", ("answer",), "READ_ONLY_USE"),
])
def test_source_denial_rollback_before_any_intent(monkeypatch, state, allowed, reason):
    events = []
    db = install_fakes(monkeypatch, events)

    def check(conn):
        assert conn is db and events == ["txn_open"]
        events.append("source_checked")
        return SimpleNamespace(state=state, allowed_uses=allowed, reason=reason)

    with pytest.raises(RuntimeError, match="KNOWLEDGE_ADMISSION_REJECTED"):
        fenced_worker.register_intent(
            tenant_id="tenant", task_id="task", handoff_id="handoff",
            subject_id="supplier", period="quarterly", source_gate=check)
    assert events == ["txn_open", "source_checked", "txn_rollback", "close"]
