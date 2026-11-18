"""Fail-closed startup contract for the standalone Temporal worker."""
import os
import pytest

from agent_memory_core.worker_identity import configured_worker_tenant


def test_temporal_strict_mode_requires_worker_tenant(monkeypatch):
    monkeypatch.setenv("MEMORY_TEMPORAL_REQUIRE_RLS", "1")
    monkeypatch.delenv("MEMORY_WORKER_TENANT", raising=False)
    with pytest.raises(RuntimeError):
        if configured_worker_tenant() is None and os.getenv("MEMORY_TEMPORAL_REQUIRE_RLS") == "1":
            raise RuntimeError("MEMORY_WORKER_TENANT is required")


def test_temporal_worker_uses_same_tenant_scope_as_database_workers(monkeypatch):
    monkeypatch.setenv("MEMORY_TEMPORAL_REQUIRE_RLS", "1")
    monkeypatch.setenv("MEMORY_WORKER_TENANT", "tenant-a")
    assert configured_worker_tenant() == "tenant-a"
