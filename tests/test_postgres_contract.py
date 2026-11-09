from pathlib import Path
import ast


def test_postgres_core_schema_has_three_layers():
    sql=(Path(__file__).resolve().parents[1]/"sql/postgresql/001_core.sql").read_text()
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE TABLE IF NOT EXISTS memory_claims" in sql
    assert "CREATE TABLE IF NOT EXISTS memory_embeddings" in sql
    assert "CREATE TABLE IF NOT EXISTS memory_edges" in sql
    assert "memory_observations" in sql


def test_age_separate_migration():
    sql=(Path(__file__).resolve().parents[1]/"sql/postgresql/002_age.sql").read_text()
    assert "CREATE EXTENSION IF NOT EXISTS age" in sql
    assert "create_graph('memory_graph')" in sql


def test_postgres_module_parses_without_optional_dependency():
    path=Path(__file__).resolve().parents[1]/"src/agent_memory_core/postgres.py"
    ast.parse(path.read_text())
