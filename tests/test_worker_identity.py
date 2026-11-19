"""Standalone worker scope validation without requiring PostgreSQL or Temporal."""
import pytest

from agent_memory_core.worker_identity import (
    configured_worker_tenant,
    assert_worker_tenant,
)


def test_strict_worker_scope_is_required(monkeypatch):
    monkeypatch.setenv("MEMORY_WORKER_REQUIRE_RLS", "1")
    monkeypatch.delenv("MEMORY_WORKER_TENANT", raising=False)
    with pytest.raises(RuntimeError, match="MEMORY_WORKER_TENANT"):
        configured_worker_tenant()
    monkeypatch.setenv("MEMORY_WORKER_TENANT", " tenant-a ")
    assert configured_worker_tenant() == "tenant-a"


def test_worker_scope_rejects_wrong_login_and_uninstalled_registry(monkeypatch):
    monkeypatch.setenv("MEMORY_WORKER_REQUIRE_RLS", "1")

    class Memory:
        def __init__(self):
            self.calls = []
        def assert_database_tenant(self, tenant):
            self.calls.append(tenant)
            if tenant != "tenant-a":
                raise PermissionError("wrong PostgreSQL LOGIN")

    db = Memory()
    assert_worker_tenant(db, "tenant-a")
    with pytest.raises(PermissionError):
        assert_worker_tenant(db, "tenant-b")
    with pytest.raises(RuntimeError, match="Tenant-bound"):
        assert_worker_tenant(db, None)
    assert db.calls == ["tenant-a", "tenant-b"]

    db.assert_database_tenant = lambda _: (_ for _ in ()).throw(
        RuntimeError("Database RLS principal mapping is not installed"))
    with pytest.raises(RuntimeError, match="RLS principal"):
        assert_worker_tenant(db, "tenant-a")


def test_legacy_development_has_no_implicit_tenant(monkeypatch):
    monkeypatch.delenv("MEMORY_WORKER_REQUIRE_RLS", raising=False)
    monkeypatch.delenv("MEMORY_WORKER_TENANT", raising=False)
    assert configured_worker_tenant() is None

    class Db:
        def assert_database_tenant(self, _):
            raise AssertionError("No tenant-bound check without tenant")

    assert_worker_tenant(Db(), None)
