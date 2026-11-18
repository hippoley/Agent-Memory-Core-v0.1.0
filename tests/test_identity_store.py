"""IdentityStore integration: two tenants, live revocation and scoped credentials."""
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_identity_store_session_membership_and_credential_lifecycle():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    import psycopg
    from agent_memory_core.identity import IdentityStore

    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    migration = root / "src/agent_memory_core/resources/005_identity_foundation.sql"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(migration.read_text(encoding="utf-8"))

    store = IdentityStore(dsn)
    try:
        suffix = uuid4().hex
        principal_a = store.create_principal(display_name="Alice")
        principal_b = store.create_principal(display_name="Bob")
        store.link_external_identity(
            principal_id=principal_a, issuer="https://issuer.example.test",
            subject="alice-" + suffix, email="alice@example.test", email_verified=True)
        store.create_tenant(
            tenant_id="tenant-a-" + suffix, display_name="Tenant A",
            owner_principal_id=principal_a)
        store.create_tenant(
            tenant_id="tenant-b-" + suffix, display_name="Tenant B",
            owner_principal_id=principal_b)
        store.add_membership(
            tenant_id="tenant-a-" + suffix, principal_id=principal_b, role="reader")

        session = store.issue_session(principal_id=principal_a)
        assert len(session["token"]) >= 40
        assert store.authenticate_session(
            token=session["token"], tenant_id="tenant-a-" + suffix)["role"] == "owner"
        assert store.authenticate_session(
            token=session["token"], tenant_id="tenant-b-" + suffix) is None

        # Role downgrade is enforced on the next request, not after token expiry.
        store.set_membership_role(
            tenant_id="tenant-a-" + suffix, principal_id=principal_a, role="reader")
        assert store.authenticate_session(
            token=session["token"], tenant_id="tenant-a-" + suffix)["role"] == "reader"
        assert store.revoke_membership(
            tenant_id="tenant-a-" + suffix, principal_id=principal_a)
        assert store.authenticate_session(
            token=session["token"], tenant_id="tenant-a-" + suffix) is None

        # Revocation invalidates the opaque session immediately.
        session_b = store.issue_session(principal_id=principal_b)
        assert store.authenticate_session(
            token=session_b["token"], tenant_id="tenant-a-" + suffix)["role"] == "reader"
        assert store.revoke_session(
            session_id=session_b["session_id"], principal_id=principal_b)
        assert store.authenticate_session(
            token=session_b["token"], tenant_id="tenant-a-" + suffix) is None

        # Credential secrets are one-time returned and are checked against current membership.
        credential = store.issue_api_credential(
            principal_id=principal_b, tenant_id="tenant-a-" + suffix,
            name="test sdk", scopes=["memory:read"])
        authenticated = store.authenticate_api_credential(secret=credential["secret"])
        assert authenticated["tenant_id"] == "tenant-a-" + suffix
        assert authenticated["scopes"] == ["memory:read"]
        assert store.revoke_api_credential(
            credential_id=credential["credential_id"], principal_id=principal_b)
        assert store.authenticate_api_credential(secret=credential["secret"]) is None
    finally:
        store.close()
