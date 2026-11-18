"""Real PostgreSQL acceptance for the identity control-plane schema.

Requires a disposable admin MEMORY_TEST_DSN. These tables are deliberately
separate from the tenant memory data plane and are not granted to memory_tenant.
"""
import os
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def test_identity_foundation_constraints_and_secret_hash_storage():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")

    import psycopg

    root = Path(__file__).resolve().parents[1]
    migration = root / "src/agent_memory_core/resources/005_identity_foundation.sql"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(migration.read_text(encoding="utf-8"))
        with conn.transaction():
            principal = uuid4()
            identity = uuid4()
            membership = uuid4()
            session = uuid4()
            credential = uuid4()
            tenant = "identity-test-" + uuid4().hex
            token_hash = bytes.fromhex("11" * 32)
            credential_hash = bytes.fromhex("22" * 32)

            conn.execute(
                """INSERT INTO memory_principals
                   (principal_id,principal_type,display_name)
                   VALUES (%s,'human','Acceptance User')""", (principal,))
            conn.execute(
                """INSERT INTO memory_external_identities
                   (identity_id,principal_id,issuer,subject,email,email_verified)
                   VALUES (%s,%s,'https://issuer.example.test','subject-1',
                           'user@example.test',true)""", (identity, principal))
            conn.execute(
                """INSERT INTO memory_tenants(tenant_id,display_name)
                   VALUES (%s,'Acceptance Tenant')""", (tenant,))
            conn.execute(
                """INSERT INTO memory_tenant_memberships
                   (membership_id,tenant_id,principal_id,role)
                   VALUES (%s,%s,%s,'owner')""", (membership, tenant, principal))
            conn.execute(
                """INSERT INTO memory_sessions
                   (session_id,principal_id,token_hash,expires_at)
                   VALUES (%s,%s,%s,clock_timestamp()+interval '1 hour')""",
                (session, principal, token_hash))
            conn.execute(
                """INSERT INTO memory_api_credentials
                   (credential_id,principal_id,tenant_id,name,secret_hash,
                    secret_prefix,scopes)
                   VALUES (%s,%s,%s,'sdk-test',%s,'amc_test',
                           ARRAY['memory:read','memory:write'])""",
                (credential, principal, tenant, credential_hash))
            conn.execute(
                """INSERT INTO memory_identity_audit_events
                   (audit_id,correlation_id,principal_id,tenant_id,action,
                    decision,reason_code)
                   VALUES (%s,'corr-test',%s,%s,'identity.acceptance','allow','test')""",
                (uuid4(), principal, tenant))

            assert conn.execute(
                "SELECT token_hash FROM memory_sessions WHERE session_id=%s",
                (session,)).fetchone()[0] == token_hash
            assert conn.execute(
                "SELECT secret_hash FROM memory_api_credentials WHERE credential_id=%s",
                (credential,)).fetchone()[0] == credential_hash
            assert conn.execute(
                "SELECT role,status,version FROM memory_tenant_memberships WHERE membership_id=%s",
                (membership,)).fetchone() == ("owner", "active", 1)

            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    """INSERT INTO memory_external_identities
                       (identity_id,principal_id,issuer,subject)
                       VALUES (%s,%s,'https://issuer.example.test','subject-1')""",
                    (uuid4(), principal))
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    """INSERT INTO memory_tenant_memberships
                       (membership_id,tenant_id,principal_id,role)
                       VALUES (%s,%s,%s,'superuser')""",
                    (uuid4(), tenant, uuid4()))

            # Revocation/version changes are persisted control-plane state.
            conn.execute(
                """UPDATE memory_tenant_memberships
                   SET role='reader',version=version+1,updated_at=clock_timestamp()
                   WHERE membership_id=%s""", (membership,))
            conn.execute(
                "UPDATE memory_sessions SET revoked_at=clock_timestamp() WHERE session_id=%s",
                (session,))
            assert conn.execute(
                "SELECT role,version FROM memory_tenant_memberships WHERE membership_id=%s",
                (membership,)).fetchone() == ("reader", 2)
            assert conn.execute(
                "SELECT revoked_at IS NOT NULL FROM memory_sessions WHERE session_id=%s",
                (session,)).fetchone()[0]
            # Transaction rolls back so this test leaves no tenant or identity rows.
            raise _RollbackIdentityAcceptance


class _RollbackIdentityAcceptance(Exception):
    pass
