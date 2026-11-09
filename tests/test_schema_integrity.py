from pathlib import Path

def test_schema_has_complete_entity_and_event_tables():
    sql=Path("sql/postgresql/001_core.sql").read_text()
    for name in ("memory_entities","memory_sources","memory_claims","memory_edges",
                 "memory_observations","memory_outbox","memory_idempotency"):
        assert "CREATE TABLE IF NOT EXISTS "+name in sql
    assert sql.count("CREATE TABLE IF NOT EXISTS memory_claims (")==1
    assert sql.count("CREATE TABLE IF NOT EXISTS memory_observations (")==1
    assert sql.count("CREATE TABLE IF NOT EXISTS memory_sources (")==1
    assert sql.count("CREATE TABLE IF NOT EXISTS memory_outbox (")==1
