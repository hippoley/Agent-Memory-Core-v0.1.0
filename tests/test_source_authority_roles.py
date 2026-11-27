"""Opt-in PostgreSQL privilege acceptance for the separate source verifier.

A real PostgreSQL admin login is needed to create test roles. These tests
verify actual denied SQL operations, rather than treating successful signed
source parsing as proof of a production trust boundary.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "sql/postgresql/security/003_knowledge_source_roles.sql"


def test_role_sql_never_runs_as_normal_migration_and_separates_authority():
    sql = SQL.read_text()
    assert "CREATE ROLE memory_source_verifier NOLOGIN" in sql
    assert "CREATE ROLE memory_source_authority NOLOGIN" in sql
    assert "GRANT SELECT ON public.memory_knowledge_source_revisions" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON public.memory_knowledge_source_revisions" in sql
    assert "FROM memory_tenant" in sql
    assert "GRANT memory_source_authority TO memory_source_verifier" not in sql
    # Role provisioning is operator-only, not a routine schema migration.
    assert not (ROOT / "src/agent_memory_core/resources/003_knowledge_source_roles.sql").exists()


@pytest.mark.integration
def test_actual_postgres_role_denies_agent_edits_but_allows_verifier_read():
    if not os.getenv("MEMORY_TEST_DSN"):
        pytest.skip("needs real PostgreSQL")
    from agent_memory_core.postgres import PostgresMemory
    db = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    try:
        db.migrate()
        # CI's test DB must permit role creation: no bypass/skipping on
        # permission errors. The source verifier role has no table ownership.
        db.conn.execute(SQL.read_text())
        def privileges(role):
            row = db.conn.execute(
                """SELECT has_table_privilege(%s,
                           'public.memory_knowledge_source_revisions','SELECT'),
                          has_table_privilege(%s,
                           'public.memory_knowledge_source_revisions','INSERT'),
                          has_table_privilege(%s,
                           'public.memory_knowledge_source_revisions','UPDATE'),
                          has_table_privilege(%s,
                           'public.memory_evidence_producer_keys','UPDATE')""",
                (role,role,role,role),
            ).fetchone()
            return row

        assert privileges("memory_source_verifier") == (True,False,False,False)
        assert privileges("memory_source_authority") == (True,True,True,False)
        # PostgreSQL SET LOCAL ROLE runs on a separate transaction and
        # proves writes are rejected by the server, not a Python if-statement.
        import psycopg
        with db.conn.transaction():
            db.conn.execute("SET LOCAL ROLE memory_source_verifier")
            assert db.conn.execute(
                "SELECT count(*) FROM memory_knowledge_source_revisions"
            ).fetchone()[0] >= 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.conn.transaction():
                db.conn.execute("SET LOCAL ROLE memory_source_verifier")
                db.conn.execute(
                    """UPDATE memory_knowledge_source_revisions
                       SET source_id='forged-by-agent' WHERE false""")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with db.conn.transaction():
                db.conn.execute("SET LOCAL ROLE memory_source_verifier")
                db.conn.execute(
                    """UPDATE memory_evidence_producer_keys
                       SET revoked_at=NULL WHERE false""")
    finally:
        db.close()
