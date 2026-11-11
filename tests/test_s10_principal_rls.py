"""S10 adversarial PostgreSQL login-identity RLS integration.

Requires a disposable local PostgreSQL with CREATEROLE privilege on the test
admin DSN. Security setup is operator-only and never runs in PostgresMemory.migrate.
"""
import os
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark=pytest.mark.integration


def test_distinct_authenticated_logins_cannot_spoof_cross_tenant_sql():
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("MEMORY_TEST_DSN required")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.webhooks import WebhookStore

    admin=PostgresMemory(dsn)
    unique=uuid4().hex[:14]
    tenant_a="s10-a-"+unique
    tenant_b="s10-b-"+unique
    role_a="mem_s10_a_"+unique
    role_b="mem_s10_b_"+unique
    password_a=uuid4().hex+uuid4().hex
    password_b=uuid4().hex+uuid4().hex
    created=[]
    db_a=db_b=None
    try:
        admin.migrate()
        admin_id_a=admin.remember(
            tenant_id=tenant_a,subject_id="bedroom",predicate="night_mode",
            value={"enabled":True},source_id="a",valid_from=datetime(2026,1,1,tzinfo=timezone.utc))
        admin_id_b=admin.remember(
            tenant_id=tenant_b,subject_id="bedroom",predicate="night_mode",
            value={"enabled":False},source_id="b",valid_from=datetime(2026,1,1,tzinfo=timezone.utc))
        with admin.conn.transaction():
            admin.conn.execute(
                "INSERT INTO memory_objects(tenant_id,object_sha256,byte_size) VALUES (%s,%s,4),(%s,%s,4)",
                (tenant_a,"a"*64,tenant_b,"b"*64))
        admin.set_predicate_cardinality(tenant_id=tenant_a,predicate="HAS_WINDOW",cardinality="many")
        admin.set_predicate_cardinality(tenant_id=tenant_b,predicate="HAS_WINDOW",cardinality="one")
        sub_b=WebhookStore(admin.conn).create(tenant_id=tenant_b,
            target_url="https://hooks.example.test/memory",
            event_types=["memory.claim.created"])
        admin.store_embedding(tenant_id=tenant_b,memory_id=admin_id_b,
            model_id="test384",vector=[1.0]+[0.0]*383)

        # Seed canonical rows and trusted policy before enabling RLS. The
        # restricted tenant role must never perform administrative setup.
        script=(Path(__file__).resolve().parents[1]/
                "sql/postgresql/security/001_principal_bound_rls.sql")
        admin.conn.execute(script.read_text(encoding="utf-8"))
        for role,password,tenant in ((role_a,password_a,tenant_a),
                                     (role_b,password_b,tenant_b)):
            admin.conn.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT PASSWORD {}").format(
                sql.Identifier(role),sql.Literal(password)))
            created.append(role)
            admin.conn.execute(sql.SQL("GRANT memory_tenant TO {}").format(
                sql.Identifier(role)))
            admin.conn.execute(
                "INSERT INTO public.memory_rls_principals(role_name,tenant_id) VALUES (%s,%s)",
                (role,tenant))

        db_a=PostgresMemory(make_conninfo(dsn,user=role_a,password=password_a))
        db_b=PostgresMemory(make_conninfo(dsn,user=role_b,password=password_b))
        db_a.assert_database_tenant(tenant_a)
        db_b.assert_database_tenant(tenant_b)
        with pytest.raises(PermissionError):
            db_a.assert_database_tenant(tenant_b)
        with pytest.raises(PermissionError):
            admin.assert_database_tenant(tenant_a)
        assert db_a.conn.execute(
            "SELECT public.memory_authenticated_tenant()").fetchone()[0]==tenant_a
        assert db_a.conn.execute(
            "SELECT id::text FROM memory_claims ORDER BY id").fetchall()==[(admin_id_a,)]
        assert db_b.conn.execute(
            "SELECT id::text FROM memory_claims ORDER BY id").fetchall()==[(admin_id_b,)]
        assert db_a.conn.execute(
            "SELECT count(*) FROM memory_embeddings").fetchone()[0]==0
        assert db_b.conn.execute(
            "SELECT count(*) FROM memory_embeddings").fetchone()[0]==1
        assert db_a.conn.execute(
            "SELECT object_sha256 FROM memory_objects").fetchall()==[("a"*64,)]
        assert db_a.conn.execute(
            "SELECT subscription_id::text FROM memory_webhook_subscriptions").fetchall()==[]
        assert db_b.conn.execute(
            "SELECT subscription_id::text FROM memory_webhook_subscriptions").fetchall()==[(sub_b,)]
        assert db_a.conn.execute(
            "SELECT predicate,cardinality FROM memory_predicate_policies").fetchall()==[("HAS_WINDOW","many")]
        assert db_b.conn.execute(
            "SELECT predicate,cardinality FROM memory_predicate_policies").fetchall()==[("HAS_WINDOW","one")]
        with pytest.raises(psycopg.Error):
            db_a.conn.execute(
                "UPDATE memory_predicate_policies SET cardinality='one' WHERE predicate='HAS_WINDOW'")

        # A caller may mutate this custom GUC, but policy ignores it entirely.
        db_a.conn.execute("SELECT set_config('app.tenant_id',%s,false)",(tenant_b,))
        assert db_a.conn.execute(
            "SELECT public.memory_authenticated_tenant()").fetchone()[0]==tenant_a
        assert db_a.conn.execute(
            "SELECT count(*) FROM memory_claims WHERE tenant_id=%s",(tenant_b,)).fetchone()[0]==0
        assert db_a.conn.execute(
            "UPDATE memory_claims SET predicate='stolen' WHERE id=%s RETURNING id",
            (admin_id_b,)).fetchall()==[]
        assert db_a.conn.execute(
            "DELETE FROM memory_claims WHERE id=%s RETURNING id",(admin_id_b,)).fetchall()==[]
        with pytest.raises(psycopg.Error):
            db_a.conn.execute(
                """INSERT INTO memory_claims
                   (id,tenant_id,subject_id,predicate,value,source_id,valid_from)
                   VALUES (%s,%s,'x','x','{}'::jsonb,'test',clock_timestamp())""",
                (uuid4(),tenant_b))
        with pytest.raises(psycopg.Error):
            db_a.conn.execute(
                """INSERT INTO memory_claim_sources(tenant_id,claim_id,source_id)
                   VALUES (%s,%s,'a')""",(tenant_a,admin_id_b))
        with pytest.raises(psycopg.Error):
            db_a.conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role_b)))

        # A legitimate write with the authenticated tenant still succeeds.
        own=db_a.remember(tenant_id=tenant_a,subject_id="office",
            predicate="noise",value={"level":"quiet"},source_id="a",
            valid_from=datetime(2026,1,1,tzinfo=timezone.utc))
        assert db_a.recall(tenant_id=tenant_a,subject_id="office")[0]["id"]==own
        with pytest.raises(psycopg.Error):
            db_a.conn.execute(
                "SELECT role_name,tenant_id FROM public.memory_rls_principals")
    finally:
        if db_a:
            db_a.close()
        if db_b:
            db_b.close()
        for role in reversed(created):
            admin.conn.execute(
                "DELETE FROM public.memory_rls_principals WHERE role_name=%s",(role,))
            admin.conn.execute(sql.SQL("REVOKE memory_tenant FROM {}").format(
                sql.Identifier(role)))
            admin.conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        admin.close()


def test_rls_sql_is_operator_only_not_automatic_migration():
    from importlib import resources
    root=resources.files("agent_memory_core").joinpath("resources")
    assert "004_principal_bound_rls.sql" not in [x.name for x in root.iterdir()]
    ddl=(Path(__file__).resolve().parents[1]/
         "sql/postgresql/security/001_principal_bound_rls.sql").read_text()
    assert "session_user::name" in ddl
    assert "current_setting('app.tenant_id'" not in ddl
    assert "CREATE POLICY memory_authenticated_tenant_policy" in ddl
