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
        principal_c = store.create_principal(display_name="Carol")
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
        store.add_membership(
            tenant_id="tenant-a-" + suffix, principal_id=principal_c, role="owner")

        with pytest.raises(ValueError, match="last active tenant owner"):
            store.set_membership_role(
                tenant_id="tenant-b-" + suffix, principal_id=principal_b, role="reader")
        with pytest.raises(ValueError, match="last active tenant owner"):
            store.revoke_membership(
                tenant_id="tenant-b-" + suffix, principal_id=principal_b)

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


def test_oidc_first_login_bootstraps_owner_workspace_atomically_and_reuses_identity():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from pathlib import Path
    import psycopg
    from agent_memory_core.identity import IdentityStore

    root = Path(__file__).resolve().parents[1]
    migration = root / "src/agent_memory_core/resources/005_identity_foundation.sql"
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(migration.read_text(encoding="utf-8"))

    store = IdentityStore(dsn)
    try:
        suffix = uuid4().hex
        issuer = "https://issuer.example.test/" + suffix
        first = store.complete_oidc_login(
            issuer=issuer, subject="subject-1", email="new-user@example.test",
            email_verified=True, display_name="New User", allow_signup=True,
            bootstrap_workspace=True, user_agent="acceptance-test")
        assert first["is_new_principal"] is True
        assert first["created_workspace"] is True
        assert len(first["token"]) >= 40
        assert len(first["tenants"]) == 1
        assert first["tenants"][0]["role"] == "owner"

        authenticated = store.authenticate_session(
            token=first["token"], tenant_id=first["tenants"][0]["tenant_id"])
        assert authenticated["principal_id"] == first["principal_id"]
        assert authenticated["role"] == "owner"

        second = store.complete_oidc_login(
            issuer=issuer, subject="subject-1", email="new-user@example.test",
            email_verified=True, display_name="New User", allow_signup=False,
            bootstrap_workspace=True)
        assert second["is_new_principal"] is False
        assert second["created_workspace"] is False
        assert second["principal_id"] == first["principal_id"]
        assert second["tenants"] == first["tenants"]
        assert second["token"] != first["token"]

        orphan = store.create_principal(display_name="Unassigned")
        store.link_external_identity(
            principal_id=orphan, issuer=issuer, subject="unassigned-subject")
        with pytest.raises(LookupError, match="no active tenant membership"):
            store.complete_oidc_login(
                issuer=issuer, subject="unassigned-subject", allow_signup=False,
                bootstrap_workspace=True)

        with pytest.raises(LookupError, match="No account is linked"):
            store.complete_oidc_login(
                issuer=issuer, subject="unknown-subject", allow_signup=False)
    finally:
        store.close()


def test_queued_execution_is_denied_after_session_or_membership_revocation():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    from agent_memory_core.identity import IdentityStore

    store = IdentityStore(dsn)
    try:
        suffix = uuid4().hex
        principal = store.create_principal(display_name="Queued Worker User")
        tenant = "tenant-queued-" + suffix
        store.create_tenant(
            tenant_id=tenant, display_name="Queued Tenant",
            owner_principal_id=principal)
        session = store.issue_session(principal_id=principal)
        membership = store.authenticate_session(
            token=session["token"], tenant_id=tenant)
        assert membership["membership_version"] == 1
        assert store.authorize_background_execution(
            principal_id=principal, tenant_id=tenant,
            session_id=session["session_id"],
            membership_version=membership["membership_version"])

        # Already queued work must stop when the session is revoked.
        assert store.revoke_session(
            session_id=session["session_id"], principal_id=principal)
        assert not store.authorize_background_execution(
            principal_id=principal, tenant_id=tenant,
            session_id=session["session_id"],
            membership_version=membership["membership_version"])

        session2 = store.issue_session(principal_id=principal)
        membership2 = store.authenticate_session(
            token=session2["token"], tenant_id=tenant)
        assert store.authorize_background_execution(
            principal_id=principal, tenant_id=tenant,
            session_id=session2["session_id"],
            membership_version=membership2["membership_version"])

        # Version changes invalidate a task carrying the old membership version.
        new_version = store.set_membership_role(
            tenant_id=tenant, principal_id=principal, role="admin")
        assert new_version == membership2["membership_version"] + 1
        assert not store.authorize_background_execution(
            principal_id=principal, tenant_id=tenant,
            session_id=session2["session_id"],
            membership_version=membership2["membership_version"])
        assert store.authorize_background_execution(
            principal_id=principal, tenant_id=tenant,
            session_id=session2["session_id"],
            membership_version=new_version)
    finally:
        store.close()
