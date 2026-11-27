"""Dual cloud candidates never bypass AMC canonical SQL revalidation."""
from datetime import datetime, timezone
from types import SimpleNamespace

from agent_memory_core.hybrid import HybridMemorySearch


class CanonicalConn:
    def execute(self, sql, params):
        if "WHERE id=ANY" in sql:
            assert params[1] == "tenant-a"
            assert len(params) == 6  # canonical tenant + bitemporal bounds
            return [("m1", "room", "status", {"value": "open"}, "source-1",
                     datetime.now(timezone.utc), datetime.now(timezone.utc))]
        raise AssertionError("unexpected SQL")


class CanonicalStore:
    conn = CanonicalConn()

    def vector_search(self, **kwargs):
        raise ConnectionError("local vector service unavailable")


class FakeCloud:
    def __init__(self, ids=None, fail=False):
        self.ids = ids or []
        self.fail = fail
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise ConnectionError("cloud unavailable")
        return [SimpleNamespace(memory_id=x) for x in self.ids]


def test_cloud_candidates_rechecked_against_canonical_records():
    aws = FakeCloud(["m1", "stale-or-wrong-tenant"])
    aliyun = FakeCloud(["m1"])
    report = HybridMemorySearch(
        CanonicalStore(), vector_buckets={"aws": aws, "aliyun": aliyun}
    ).search_detailed(tenant_id="tenant-a", query="", embedding=[0.1] * 384,
                      model_id="model-v1", limit=5)
    assert [h.memory_id for h in report.hits] == ["m1"]
    assert report.hits[0].channels == ("vector_aliyun", "vector_aws")
    assert report.channels_used == ("vector_aliyun", "vector_aws")
    assert "vector" in report.degraded_channels
    assert aws.calls[0]["tenant_id"] == aliyun.calls[0]["tenant_id"] == "tenant-a"


def test_cloud_failure_does_not_fake_success():
    report = HybridMemorySearch(
        CanonicalStore(), vector_buckets={"aws": FakeCloud(fail=True),
                                          "aliyun": FakeCloud(["m1"])}
    ).search_detailed(tenant_id="tenant-a", query="", embedding=[0.1] * 384,
                      model_id="model-v1")
    assert report.hits[0].memory_id == "m1"
    assert "vector_aws" in report.degraded_channels
    assert "vector_aliyun" in report.channels_used


def test_unknown_provider_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown"):
        HybridMemorySearch(CanonicalStore(), vector_buckets={"untrusted": FakeCloud()})
