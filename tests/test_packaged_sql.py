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
        "001_core.sql","002_webhooks.sql","003_outbox_recovery.sql",
        "004_predicate_cardinality.sql","005_identity_foundation.sql",
        "006_durable_tasks.sql","007_durable_task_worker_leases.sql",
        "008_durable_task_effect_reconciliation.sql","009_temporal_dispatch.sql",
        "010_evidence_provenance.sql","011_agent_handoff.sql",
        "012_knowledge_source_revisions.sql"
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
        "004_predicate_cardinality.sql","005_identity_foundation.sql",
        "006_durable_tasks.sql","007_durable_task_worker_leases.sql",
        "008_durable_task_effect_reconciliation.sql","009_temporal_dispatch.sql",
        "010_evidence_provenance.sql","011_agent_handoff.sql",
        "012_knowledge_source_revisions.sql"
    ]

def test_packaged_identity_foundation_migration_matches_repository_sql():
    packaged=resources.files("agent_memory_core").joinpath("resources/005_identity_foundation.sql").read_text()
    canonical=(Path(__file__).resolve().parents[1]/"sql/postgresql/005_identity_foundation.sql").read_text()
    assert packaged==canonical
    assert "memory_tenant_memberships" in packaged
    assert "token_hash" in packaged


def test_packaged_durable_task_migration_matches_repository_sql():
    packaged=resources.files("agent_memory_core").joinpath("resources/006_durable_tasks.sql").read_text()
    canonical=(Path(__file__).resolve().parents[1]/"sql/postgresql/006_durable_tasks.sql").read_text()
    assert packaged==canonical
    assert "memory_task_checkpoints" in packaged
    assert "memory_task_effects" in packaged


def test_packaged_worker_lease_migration_matches_repository_sql():
    packaged=resources.files("agent_memory_core").joinpath("resources/007_durable_task_worker_leases.sql").read_text()
    canonical=(Path(__file__).resolve().parents[1]/"sql/postgresql/007_durable_task_worker_leases.sql").read_text()
    assert packaged==canonical
    assert "lease_epoch" in packaged
    assert "memory_durable_tasks_lease_shape" in packaged
    assert "IF NOT EXISTS" in packaged


def test_packaged_external_effect_migration_matches_repository_sql():
    packaged=resources.files("agent_memory_core").joinpath("resources/008_durable_task_effect_reconciliation.sql").read_text()
    canonical=(Path(__file__).resolve().parents[1]/"sql/postgresql/008_durable_task_effect_reconciliation.sql").read_text()
    assert packaged==canonical
    assert "'unknown'" in packaged
    assert "'reconciling'" in packaged
    assert "request jsonb" in packaged


def test_packaged_temporal_dispatch_migration_matches_repository_sql():
    packaged=resources.files("agent_memory_core").joinpath("resources/009_temporal_dispatch.sql").read_text()
    canonical=(Path(__file__).resolve().parents[1]/"sql/postgresql/009_temporal_dispatch.sql").read_text()
    assert packaged==canonical
    assert "memory_temporal_dispatches" in packaged
    assert "lease_epoch" in packaged



def test_packaged_evidence_provenance_migration_matches_repository_sql():
    packaged=resources.files("agent_memory_core").joinpath("resources/010_evidence_provenance.sql").read_text()
    canonical=(Path(__file__).resolve().parents[1]/"sql/postgresql/010_evidence_provenance.sql").read_text()
    assert packaged==canonical
    assert "memory_evidence_producer_keys" in packaged
    assert "memory_evidence_authority_grants" in packaged
    assert "memory_evidence_nonces" in packaged
    assert "memory_evidence_facts" in packaged
    assert "producer_issuer" in packaged
    assert "allow_authoritative" in packaged


def test_agent_handoff_migration_matches_packaged_resource():
    packaged = resources.files("agent_memory_core").joinpath(
        "resources/011_agent_handoff.sql").read_text()
    canonical = (Path(__file__).resolve().parents[1] /
                 "sql/postgresql/011_agent_handoff.sql").read_text()
    assert packaged == canonical
    assert "memory_agent_handoffs" in packaged
    assert "payload_sha256" in packaged


def test_knowledge_source_revision_migration_matches_packaged_resource():
    packaged = resources.files("agent_memory_core").joinpath(
        "resources/012_knowledge_source_revisions.sql").read_text()
    canonical = (Path(__file__).resolve().parents[1] /
                 "sql/postgresql/012_knowledge_source_revisions.sql").read_text()
    assert packaged == canonical
    assert "REFERENCES memory_evidence_producer_keys" in packaged
    assert "REVOKE ALL" in packaged
    assert "memory_knowledge_source_revisions" in packaged
