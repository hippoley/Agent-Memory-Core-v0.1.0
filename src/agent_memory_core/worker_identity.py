"""Bind standalone workers to the PostgreSQL-authenticated tenant.

A tenant identifier in an event, environment variable or HTTP request is not
authorization. The database login is the authority: the operator-installed
principal-bound RLS registry maps session_user to exactly one tenant.
"""
from __future__ import annotations

import os


def configured_worker_tenant() -> str | None:
    """Resolve a dedicated single-tenant worker; reject incomplete strict mode."""
    tenant = os.getenv("MEMORY_WORKER_TENANT", "").strip()
    strict = os.getenv("MEMORY_WORKER_REQUIRE_RLS", "0") == "1"
    if strict and not tenant:
        raise RuntimeError("MEMORY_WORKER_TENANT is required with MEMORY_WORKER_REQUIRE_RLS=1")
    return tenant or None


def assert_worker_tenant(db, tenant_id: str | None) -> None:
    """Fail closed for every tenant-bound worker before it consumes any work.

    Legacy local development may run without a dedicated tenant. Production
    must configure both MEMORY_WORKER_TENANT and MEMORY_WORKER_REQUIRE_RLS=1.
    """
    strict = os.getenv("MEMORY_WORKER_REQUIRE_RLS", "0") == "1"
    if strict and not tenant_id:
        raise RuntimeError("Tenant-bound worker required")
    if tenant_id is None:
        return
    # Never catch an RLS error here: uninstalled registry, privileged LOGIN,
    # tenant mismatch, or missing database grants must stop the worker.
    db.assert_database_tenant(tenant_id)


def assert_current_task_creator_write_authority(conn, *, tenant_id: str, task_id: str) -> None:
    """Canonical strict-task permission gate for Worker and terminal API writes.

    This checks CURRENT membership. It is not proof of delegated Agent-B or
    gateway authorization, and cannot eliminate remote-call TOCTOU.
    """
    from .worker_leases import LeaseConflict

    allowed = conn.execute(
        """SELECT 1 FROM memory_durable_tasks t
           JOIN memory_principals p ON p.principal_id=t.created_by_principal_id
           JOIN memory_tenant_memberships m
             ON m.principal_id=p.principal_id AND m.tenant_id=t.tenant_id
           JOIN memory_tenants tenant ON tenant.tenant_id=t.tenant_id
           WHERE t.tenant_id=%s AND t.task_id=%s::uuid
             AND p.status='active' AND tenant.status='active'
             AND m.status='active'
             AND m.role IN ('owner','admin','editor')""",
        (tenant_id, task_id),
    ).fetchone()
    if allowed is None:
        raise LeaseConflict("Task creator is no longer authorized for external effects")
