from importlib import resources
from pathlib import Path


def test_packaged_schema_equals_repository_schema():
    packaged = resources.files("agent_memory_core").joinpath("resources/001_core.sql").read_text()
    canonical = (Path(__file__).resolve().parents[1] / "sql/postgresql/001_core.sql").read_text()
    assert packaged == canonical


def test_packaged_recovery_migration_matches_repository_sql():
    packaged=resources.files("agent_memory_core").joinpath("resources/003_outbox_recovery.sql").read_text()
    canonical=(Path(__file__).resolve().parents[1]/"sql/postgresql/003_outbox_recovery.sql").read_text()
    assert packaged==canonical
    assert "dead_lettered_at" in packaged
    assert "max_attempts" in packaged


def test_migration_runner_discovers_ordered_sql_resources():
    root=resources.files("agent_memory_core").joinpath("resources")
    migrations=sorted(x.name for x in root.iterdir()
        if x.name.endswith(".sql") and x.name[:3].isdigit())
    assert migrations==[
        "001_core.sql","002_webhooks.sql","003_outbox_recovery.sql"
    ]

def test_packaged_predicate_policy_migration_matches_repository_sql():
    packaged=resources.files("agent_memory_core").joinpath("resources/004_predicate_cardinality.sql").read_text()
    canonical=(Path(__file__).resolve().parents[1]/"sql/postgresql/004_predicate_cardinality.sql").read_text()
    assert packaged==canonical
    assert "cardinality IN ('one','many')" in packaged


def test_migration_runner_discovers_predicate_policy_migration():
    root=resources.files("agent_memory_core").joinpath("resources")
    migrations=sorted(x.name for x in root.iterdir()
        if x.name.endswith(".sql") and x.name[:3].isdigit())
    assert migrations==[
        "001_core.sql","002_webhooks.sql","003_outbox_recovery.sql",
        "004_predicate_cardinality.sql"
    ]
