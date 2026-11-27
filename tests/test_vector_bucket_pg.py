"""Real PostgreSQL gate for optional cloud candidate retrieval.

Requires MEMORY_TEST_DSN and a migrated PostgreSQL/pgvector/AGE instance.
This does not require cloud keys; it tests the canonical database boundary.
"""
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

if not os.getenv("MEMORY_TEST_DSN"):
    pytest.skip("MEMORY_TEST_DSN absent: real PostgreSQL cloud boundary not tested",
                allow_module_level=True)

from agent_memory_core.postgres import PostgresMemory
from agent_memory_core.hybrid import HybridMemorySearch


class CloudCandidates:
    def __init__(self, *memory_ids):
        self.memory_ids = memory_ids
        self.calls = 0

    def query(self, **kwargs):
        self.calls += 1
        return [SimpleNamespace(memory_id=x) for x in self.memory_ids]


def test_real_postgres_blocks_stale_cross_tenant_and_malformed_cloud_candidates():
    store = PostgresMemory(os.environ["MEMORY_TEST_DSN"])
    tenant = "cloud-gate-" + uuid4().hex
    other_tenant = "untrusted-" + uuid4().hex
    try:
        store.migrate()
        initial = store.remember(
            tenant_id=tenant, subject_id="living-room", predicate="window_angle",
            value={"degrees": 35}, source_id="contract-v1",
            valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
        other = store.remember(
            tenant_id=other_tenant, subject_id="another-room", predicate="window_angle",
            value={"degrees": 70}, source_id="contract-v1",
            valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
        current = store.remember(
            tenant_id=tenant, subject_id="living-room", predicate="window_angle",
            value={"degrees": 52.5}, source_id="contract-v2",
            valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            replaces_id=initial)
        cloud = CloudCandidates(initial, other, "malformed-id", current)
        search = HybridMemorySearch(store, vector_buckets={"aws": cloud})
        report = search.search_detailed(
            tenant_id=tenant, query="", embedding=[0.01] * 384,
            model_id="test384", cloud_providers=("aws",), limit=10)
        assert [h.memory_id for h in report.hits] == [current]
        assert report.hits[0].value == {"degrees": 52.5}
        assert "malformed candidate IDs" in report.degraded_channels["vector_aws"]
        assert cloud.calls == 1
        no_cloud = search.search_detailed(
            tenant_id=tenant, query="", embedding=[0.01] * 384,
            model_id="test384", cloud_providers=(), limit=10)
        assert no_cloud.hits == ()
        assert cloud.calls == 1
    finally:
        store.close()
