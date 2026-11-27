"""Dual cloud candidates never bypass AMC canonical SQL revalidation."""
from datetime import datetime, timezone
from types import SimpleNamespace

from agent_memory_core.hybrid import HybridMemorySearch

VALID_ID = "11111111-1111-4111-8111-111111111111"
STALE_ID = "22222222-2222-4222-8222-222222222222"


class CanonicalConn:
    def execute(self, sql, params):
        if "WHERE id=ANY" in sql:
            assert params[1] == "tenant-a"
            assert len(params) == 6  # canonical tenant + bitemporal bounds
            assert all(len(mid) == 36 for mid in params[0])
            if VALID_ID not in params[0]:
                return []
            return [(VALID_ID, "room", "status", {"value": "open"}, "source-1",
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
    aws = FakeCloud([VALID_ID, STALE_ID])
    aliyun = FakeCloud([VALID_ID])
    report = HybridMemorySearch(
        CanonicalStore(), vector_buckets={"aws": aws, "aliyun": aliyun}
    ).search_detailed(tenant_id="tenant-a", query="", embedding=[0.1] * 384,
                      model_id="model-v1", limit=5)
    assert [h.memory_id for h in report.hits] == [VALID_ID]
    assert report.hits[0].channels == ("vector_aliyun", "vector_aws")
    assert report.channels_used == ("vector_aliyun", "vector_aws")
    assert "vector" in report.degraded_channels
    assert aws.calls[0]["tenant_id"] == aliyun.calls[0]["tenant_id"] == "tenant-a"


def test_cloud_failure_does_not_fake_success():
    report = HybridMemorySearch(
        CanonicalStore(), vector_buckets={"aws": FakeCloud(fail=True),
                                          "aliyun": FakeCloud([VALID_ID])}
    ).search_detailed(tenant_id="tenant-a", query="", embedding=[0.1] * 384,
                      model_id="model-v1")
    assert report.hits[0].memory_id == VALID_ID
    assert "vector_aws" in report.degraded_channels
    assert "vector_aliyun" in report.channels_used


def test_unknown_provider_is_rejected():
    import pytest
    with pytest.raises(ValueError, match="unknown"):
        HybridMemorySearch(CanonicalStore(), vector_buckets={"untrusted": FakeCloud()})

def test_azure_candidates_are_canonically_revalidated_and_fail_independently():
    azure = FakeCloud([VALID_ID, STALE_ID])
    report = HybridMemorySearch(
        CanonicalStore(), vector_buckets={
            "azure": azure, "aws": FakeCloud(fail=True),
            "aliyun": FakeCloud([VALID_ID]),
        }
    ).search_detailed(
        tenant_id="tenant-a", query="", embedding=[0.1] * 384,
        model_id="model-v1", limit=5)
    assert [hit.memory_id for hit in report.hits] == [VALID_ID]
    assert report.hits[0].channels == ("vector_aliyun", "vector_azure")
    assert "vector_aws" in report.degraded_channels
    assert "vector_azure" in report.channels_used

def test_mirrored_cloud_hits_do_not_multiply_evidence_score():
    one = HybridMemorySearch(
        CanonicalStore(), vector_buckets={"aws": FakeCloud([VALID_ID])}
    ).search_detailed(tenant_id="tenant-a", query="", embedding=[0.1] * 384,
                      model_id="model-v1")
    three = HybridMemorySearch(
        CanonicalStore(), vector_buckets={
            "aws": FakeCloud([VALID_ID]),
            "aliyun": FakeCloud([VALID_ID]),
            "azure": FakeCloud([VALID_ID]),
        }
    ).search_detailed(tenant_id="tenant-a", query="", embedding=[0.1] * 384,
                      model_id="model-v1")
    assert one.hits[0].score == three.hits[0].score
    assert len(three.hits[0].channels) == 3


def test_malformed_provider_id_is_rejected_before_postgres_uuid_cast():
    cloud = FakeCloud(["not-a-uuid", VALID_ID])
    report = HybridMemorySearch(
        CanonicalStore(), vector_buckets={"azure": cloud}
    ).search_detailed(tenant_id="tenant-a", query="",
                      embedding=[0.1] * 384, model_id="model-v1")
    assert [hit.memory_id for hit in report.hits] == [VALID_ID]
    assert "malformed candidate IDs" in report.degraded_channels["vector_azure"]
