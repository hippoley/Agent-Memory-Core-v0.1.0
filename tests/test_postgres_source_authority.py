"""Knowledge verification reads the existing service-principal issuer registry.

The PG test requires a real PostgreSQL connection; test-only admin creates
a tenant, principal and pinned key. The verifier never receives Agent-supplied
key policy, and a revocation made in PG invalidates the *same* signed source.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_memory_core.postgres_source_authority import review_registry_handoff_claim
from test_source_attestation import accepted_packet, envelope, NOW, TENANT, SUBJECT, PREDICATE, SOURCE


class FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeRegistryDB:
    def __init__(self, row):
        self.row = row
        self.queries = []

    def transaction(self):
        return nullcontext()

    def execute(self, query, params):
        self.queries.append((query, params))
        assert "FOR SHARE OF revision,tenant,producer,principal" in query
        assert params[:3] == (TENANT, SUBJECT, PREDICATE)
        return FakeResult(self.row)


def test_registry_denies_undefined_or_revoked_authority():
    signer = Ed25519PrivateKey.generate()
    decision = review_registry_handoff_claim(
        conn=FakeRegistryDB(None), accepted_handoff=accepted_packet(),
        signed_source_envelope=envelope(signer), tenant_id=TENANT,
        subject_id=SUBJECT, predicate=PREDICATE,
        proposed_value={"period": "quarterly"}, now=NOW)
    assert decision.reason == "SOURCE_AUTHORITY_NOT_ACTIVE"
    assert not decision.permitted


def test_registry_pins_key_not_caller_supplied_signature():
    signer = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    pub = signer.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    db = FakeRegistryDB((SOURCE, "contract-registry", "key-1", pub))
    params = dict(
        conn=db, accepted_handoff=accepted_packet(),
        tenant_id=TENANT, subject_id=SUBJECT,
        predicate=PREDICATE, proposed_value={"period": "quarterly"},
        now=NOW)
    valid = review_registry_handoff_claim(
        signed_source_envelope=envelope(signer), **params)
    assert valid.allowed_uses == ("answer",)
    invalid = review_registry_handoff_claim(
        signed_source_envelope=envelope(attacker), **params)
    assert invalid.reason == "SOURCE_SIGNATURE_INVALID"
    assert not invalid.permitted


@pytest.mark.integration
def test_real_postgres_source_key_revoke_and_revision_change():
    dsn = os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("requires real PostgreSQL")
    from agent_memory_core.postgres import PostgresMemory
    db = PostgresMemory(dsn)
    try:
        db.migrate()
        principal = str(uuid4())
        tenant = "knowledge-tenant-" + uuid4().hex
        issuer = "contract-registry-" + uuid4().hex
        signer = Ed25519PrivateKey.generate()
        pub = signer.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        now = datetime.now(timezone.utc)
        key_id = "key-01"
        with db.conn.transaction():
            db.conn.execute(
                "INSERT INTO memory_tenants(tenant_id,display_name) VALUES(%s,%s)",
                (tenant, tenant))
            db.conn.execute(
                """INSERT INTO memory_principals(principal_id,principal_type,status)
                   VALUES(%s::uuid,'service','active')""", (principal,))
            db.conn.execute(
                """INSERT INTO memory_evidence_producer_keys
                   (issuer,key_id,principal_id,public_key,audience,not_before)
                   VALUES(%s,%s,%s::uuid,%s,'amc-knowledge-source',%s)""",
                (issuer, key_id, principal, pub, now - timedelta(minutes=5)))
            db.conn.execute(
                """INSERT INTO memory_knowledge_source_revisions
                   (tenant_id,subject_id,predicate,source_id,issuer,key_id)
                   VALUES(%s,%s,%s,%s,%s,%s)""",
                (tenant, SUBJECT, PREDICATE, SOURCE, issuer, key_id))
        # Use a real signer, and a source revision registered in PostgreSQL.
        import base64
        import json
        body = {
            "schema": "amc.source-attestation.v1", "issuer": issuer,
            "tenant_id": tenant, "subject_id": SUBJECT,
            "predicate": PREDICATE, "source_id": SOURCE,
            "value": {"period": "quarterly"},
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=2)).isoformat(),
        }
        signature = base64.urlsafe_b64encode(signer.sign(json.dumps(
            body, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode())).rstrip(b"=").decode()
        receipt = {"body": body, "signature": signature}
        common = dict(
            conn=db.conn, accepted_handoff=accepted_packet(),
            signed_source_envelope=receipt, tenant_id=tenant,
            subject_id=SUBJECT, predicate=PREDICATE,
            proposed_value={"period": "quarterly"})
        correct = review_registry_handoff_claim(**common)
        assert correct.allowed_uses == ("answer",)

        # Real two-connection race barrier: nested verification may release
        # its savepoint, but PostgreSQL FOR SHARE locks must survive until
        # the OUTER intent transaction commits.
        import psycopg
        competing = PostgresMemory(dsn)
        try:
            with db.conn.transaction():
                locked = review_registry_handoff_claim(**common)
                assert locked.allowed_uses == ("answer",)
                # An independent revoker cannot modify the pinned revision
                # while the verifier's outer effect-intent tx is active.
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    with competing.conn.transaction():
                        competing.conn.execute(
                            "SET LOCAL lock_timeout='100ms'")
                        competing.conn.execute(
                            """UPDATE memory_knowledge_source_revisions
                               SET source_id=%s
                               WHERE tenant_id=%s AND subject_id=%s AND predicate=%s""",
                            ("contract:revoked-between-check-and-use",
                             tenant, SUBJECT, PREDICATE))
            # The revoker CAN act after the outer transaction commits.
            with competing.conn.transaction():
                competing.conn.execute(
                    """UPDATE memory_knowledge_source_revisions
                       SET source_id=%s
                       WHERE tenant_id=%s AND subject_id=%s AND predicate=%s""",
                    ("contract:revoked-between-check-and-use",
                     tenant, SUBJECT, PREDICATE))
        finally:
            competing.close()
        denied_after = review_registry_handoff_claim(**common)
        assert denied_after.reason == "SOURCE_REVISION_NOT_ACTIVE"

        with db.conn.transaction():
            db.conn.execute(
                """UPDATE memory_knowledge_source_revisions
                   SET source_id=%s WHERE tenant_id=%s AND subject_id=%s AND predicate=%s""",
                ("contract:v4", tenant, SUBJECT, PREDICATE))
        stale = review_registry_handoff_claim(**common)
        assert stale.reason == "SOURCE_REVISION_NOT_ACTIVE"
        with db.conn.transaction():
            db.conn.execute(
                """UPDATE memory_knowledge_source_revisions
                   SET source_id=%s WHERE tenant_id=%s AND subject_id=%s AND predicate=%s""",
                (SOURCE, tenant, SUBJECT, PREDICATE))
            db.conn.execute(
                """UPDATE memory_evidence_producer_keys SET revoked_at=clock_timestamp()
                   WHERE issuer=%s AND key_id=%s""",
                (issuer, key_id))
        revoked = review_registry_handoff_claim(**common)
        assert revoked.reason == "SOURCE_AUTHORITY_NOT_ACTIVE"
    finally:
        db.close()
