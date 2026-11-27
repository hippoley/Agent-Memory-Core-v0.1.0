"""US-C02 principal-bound handoff: actual PostgreSQL + distinct API credentials.

Legacy descriptive labels are permitted for backward compatibility, but MUST
never be reported as authenticated delegation.
"""
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_principal_bound_handoff_rejects_wrong_identity_and_revocation():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from fastapi.testclient import TestClient
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.identity import IdentityStore
    from agent_memory_core.api import create_app

    # Existing production migration path creates both identity and handoff tables.
    db = PostgresMemory(dsn)
    try:
        db.migrate()
    finally:
        db.close()

    identity = IdentityStore(dsn)
    try:
        owner = identity.create_principal(principal_type="service", display_name="A")
        target = identity.create_principal(principal_type="service", display_name="B")
        stranger = identity.create_principal(principal_type="service", display_name="C")
        tenant = "c02-delegation-" + uuid4().hex
        identity.create_tenant(tenant_id=tenant, display_name="C02",
                               owner_principal_id=owner)
        identity.add_membership(tenant_id=tenant, principal_id=target, role="editor")
        identity.add_membership(tenant_id=tenant, principal_id=stranger, role="editor")
        def credential(principal, label):
            return identity.issue_api_credential(
                principal_id=principal, tenant_id=tenant, name=label,
                scopes=["memory:read", "memory:write"])
        a = credential(owner, "creator")
        b = credential(target, "receiver")
        c = credential(stranger, "wrong")
    finally:
        identity.close()

    app = create_app(
        store_factory=lambda: PostgresMemory(dsn),
        identity_store_factory=lambda: IdentityStore(dsn))
    def headers(secret):
        return {"Authorization": "Bearer " + secret,
                "X-Memory-Tenant": tenant}

    with TestClient(app) as client:
        t = client.post("/v1/tasks", headers=headers(a["secret"]), json={
            "task_type": "test.principal.handoff",
            "input": {"goal": "handoff without physical effects"},
        })
        assert t.status_code == 201, t.text
        task_id = t.json()["task_id"]

        offer = {"task_id": task_id, "subject_ids": ["c02-empty-context"],
                 "token_budget": 4096, "source_agent": "A", "target_agent": "B",
                 "target_principal_id": target}
        created = client.post("/v1/handoffs", headers=headers(a["secret"]),
                              json=offer)
        assert created.status_code == 201, created.text
        pack = created.json()["payload"]
        assert pack["delegation"]["source_principal_id"] == owner
        assert pack["delegation"]["target_principal_id"] == target
        assert pack["delegation"]["scope"] == "context_accept_only"
        handoff_id = created.json()["handoff_id"]

        for token in (a["secret"], c["secret"]):
            denied = client.post(f"/v1/handoffs/{handoff_id}/accept",
                                 headers=headers(token), json={"target_agent": "B"})
            assert denied.status_code == 403, denied.text

        accepted = client.post(f"/v1/handoffs/{handoff_id}/accept",
                               headers=headers(b["secret"]), json={"target_agent": "B"})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["principal_bound"] is True

        replay = client.post(f"/v1/handoffs/{handoff_id}/accept",
                             headers=headers(b["secret"]), json={"target_agent": "B"})
        assert replay.status_code == 200, replay.text
        assert replay.json()["idempotent"] is True

        # Old label-only handoff must remain explicitly non-principal-bound.
        legacy_offer = {k:v for k,v in offer.items() if k != "target_principal_id"}
        legacy = client.post("/v1/handoffs", headers=headers(a["secret"]),
                             json=legacy_offer)
        assert legacy.status_code == 201, legacy.text
        legacy_accept = client.post(
            f"/v1/handoffs/{legacy.json()['handoff_id']}/accept",
            headers=headers(b["secret"]), json={"target_agent": "B"})
        assert legacy_accept.status_code == 200, legacy_accept.text
        assert legacy_accept.json()["principal_bound"] is False

        changed = client.post("/v1/handoffs", headers=headers(a["secret"]),
                              json=offer)
        assert changed.status_code == 201, changed.text
        changed_id = changed.json()["handoff_id"]

        # Task creator cannot impersonate a different source Principal.
        forbidden = client.post("/v1/handoffs", headers=headers(c["secret"]),
                                json=offer)
        assert forbidden.status_code == 403, forbidden.text

        # Missing auth (legacy bearer impersonation) must never create bound offers.
        no_auth = client.post("/v1/handoffs", json=offer)
        assert no_auth.status_code == 401, no_auth.text

    # Existing identity store bumps membership version on any role change.
    identity = IdentityStore(dsn)
    try:
        identity.set_membership_role(
            tenant_id=tenant, principal_id=target, role="admin")
    finally:
        identity.close()
    with TestClient(app) as client:
        stale = client.post(f"/v1/handoffs/{changed_id}/accept",
                            headers=headers(b["secret"]), json={"target_agent": "B"})
        assert stale.status_code == 403, stale.text

        fresh = client.post("/v1/handoffs", headers=headers(a["secret"]), json=offer)
        assert fresh.status_code == 201, fresh.text
        fresh_id = fresh.json()["handoff_id"]

    identity = IdentityStore(dsn)
    try:
        assert identity.revoke_api_credential(
            credential_id=b["credential_id"], principal_id=target)
    finally:
        identity.close()
    with TestClient(app) as client:
        revoked = client.post(f"/v1/handoffs/{fresh_id}/accept",
                              headers=headers(b["secret"]), json={"target_agent": "B"})
        assert revoked.status_code == 401, revoked.text


def test_principal_bound_mode_rejects_legacy_server_and_invalid_uuid():
    from fastapi.testclient import TestClient
    from agent_memory_core.api import create_app
    app = create_app(api_key="x"*32, tenant_id="legacy-only",
                     store_factory=lambda: None)
    with TestClient(app) as client:
        offer = {"task_id": str(uuid4()), "subject_ids": ["x"], "source_agent": "A",
                 "target_agent": "B", "target_principal_id": str(uuid4())}
        denied = client.post("/v1/handoffs",
                             headers={"Authorization": "Bearer " + "x"*32}, json=offer)
        assert denied.status_code == 403, denied.text
        offer["target_principal_id"] = "not-a-uuid"
        bad = client.post("/v1/handoffs",
                          headers={"Authorization": "Bearer " + "x"*32}, json=offer)
        assert bad.status_code in (403, 422), bad.text
