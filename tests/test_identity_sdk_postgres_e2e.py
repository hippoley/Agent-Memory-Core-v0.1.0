"""Real PostgreSQL + HTTP ASGI + public SDK credential lifecycle acceptance.

This test uses the real FastAPI application and PostgreSQL-backed control and
memory stores. The in-process transport is only the HTTP socket substitute,
not a fake identity or memory implementation.
"""
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_external_sdk_credential_full_memory_lifecycle_against_real_postgres():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    httpx=pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from agent_memory_core.identity import IdentityStore
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.api import create_app
    from agent_memory_core.sdk import MemoryClient, MemoryAPIError
    from pathlib import Path
    import psycopg

    migration=(Path(__file__).resolve().parents[1]
               /"src/agent_memory_core/resources/005_identity_foundation.sql")
    with psycopg.connect(dsn,autocommit=True) as conn:
        conn.execute(migration.read_text(encoding="utf-8"))
    db=PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()

    suffix=uuid4().hex
    tenant="api-sdk-e2e-"+suffix
    other_tenant="api-sdk-foreign-"+suffix
    identity=IdentityStore(dsn)
    try:
        owner=identity.create_principal(display_name="SDK E2E Owner")
        other_owner=identity.create_principal(display_name="SDK E2E Other")
        identity.create_tenant(tenant_id=tenant,display_name="SDK Test",
                               owner_principal_id=owner)
        identity.create_tenant(tenant_id=other_tenant,display_name="Foreign",
                               owner_principal_id=other_owner)
        session=identity.issue_session(principal_id=owner)
    finally:
        identity.close()

    app=create_app(api_key="dev-single-tenant-legacy-key-123456",
                   tenant_id="legacy-dev",store_factory=lambda: PostgresMemory(dsn),
                   identity_store_factory=lambda:IdentityStore(dsn))
    browser=TestClient(app)
    browser.cookies.set("amc_session",session["token"])
    browser.cookies.set("amc_csrf","csrf-e2e")
    headers={"X-Memory-Tenant":tenant,"X-CSRF-Token":"csrf-e2e"}
    issue=browser.post("/v1/identity/api-credentials",headers=headers,json={
        "name":"External Agent","scopes":["memory:read","memory:write"],
        "expires_in_days":30})
    assert issue.status_code==201,issue.text
    issued=issue.json()
    assert issued["tenant_id"]==tenant and issued["secret"].startswith("amc_")

    # Use a separate HTTP client with no session cookies. Otherwise the
    # authentication middleware would prioritize the browser's session over
    # the SDK Bearer credential, silently invalidating this acceptance test.
    external=TestClient(app)
    # The network adapter forwards genuine HTTP requests into the actual
    # ASGI service. No in-memory fake IdentityStore or fake Postgres is used.
    def transport(request):
        response=external.request(
            request.method,str(request.url),
            headers=dict(request.headers),
            content=request.content if request.content else None,
            follow_redirects=False)
        return httpx.Response(response.status_code,headers=dict(response.headers),
                              content=response.content)

    with MemoryClient("http://testserver",issued["secret"],tenant_id=tenant,
                      transport=httpx.MockTransport(transport)) as sdk:
        assert sdk.whoami()["tenant_id"]==tenant
        assert sdk.workspaces()["items"][0]["tenant_id"]==tenant
        created=sdk.remember(
            subject_id="real-external-sdk",predicate="language",
            value={"language":"Chinese"},source_id="sdk:real-postgres",
            valid_from="2026-11-18T00:00:00Z",
            idempotency_key="sdk-e2e-"+suffix)
        memory_id=created["id"]
        repeated=sdk.remember(
            subject_id="real-external-sdk",predicate="language",
            value={"language":"Chinese"},source_id="sdk:real-postgres",
            valid_from="2026-11-18T00:00:00Z",
            idempotency_key="sdk-e2e-"+suffix)
        assert repeated["id"]==memory_id
        assert any(m["id"]==memory_id for m in sdk.list_memories()["items"])
        assert sdk.recall("real-external-sdk")["items"][0]["id"]==memory_id
        assert sdk.forget(memory_id)["forgotten"]==[memory_id]
        assert not sdk.recall("real-external-sdk")["items"]
        assert any(m["id"]==memory_id for m in sdk.list_memories(
            include_superseded=True)["items"])

    with MemoryClient("http://testserver",issued["secret"],tenant_id=other_tenant,
                      transport=httpx.MockTransport(transport)) as other:
        with pytest.raises(MemoryAPIError) as exc:
            other.list_memories(include_superseded=True)
        assert exc.value.status_code==401

    revoked=browser.delete(
        "/v1/identity/api-credentials/"+issued["credential_id"],headers=headers)
    assert revoked.status_code==200,revoked.text
    with MemoryClient("http://testserver",issued["secret"],tenant_id=tenant,
                      transport=httpx.MockTransport(transport)) as sdk:
        with pytest.raises(MemoryAPIError) as exc:
            sdk.whoami()
        assert exc.value.status_code==401

    db=PostgresMemory(dsn)
    try:
        count=db.conn.execute(
            """SELECT count(*) FROM memory_claims
               WHERE tenant_id=%s AND subject_id='real-external-sdk'""",
            (tenant,)).fetchone()[0]
        assert count==1
        events=db.conn.execute(
            """SELECT count(*) FROM memory_outbox
               WHERE tenant_id=%s AND aggregate_id=%s::uuid""",
            (tenant,memory_id)).fetchone()[0]
        assert events>=1
    finally:
        db.close()
