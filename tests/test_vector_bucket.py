"""Offline contract tests: no AWS/Aliyun credentials or network required."""
import pytest

from agent_memory_core.vector_bucket import (
    VectorBucketAdapter, VectorBucketConfig,
)


class FakeVectors:
    def __init__(self):
        self.writes = []
        self.queries = []
        self.deletions = []
        self.reply = {"vectors": []}

    def put_vectors(self, **kwargs):
        self.writes.append(kwargs)

    def query_vectors(self, **kwargs):
        self.queries.append(kwargs)
        return self.reply

    def delete_vectors(self, **kwargs):
        self.deletions.append(kwargs)


@pytest.mark.parametrize("provider,endpoint", [
    ("aws", None),
    ("aliyun", "https://mybucket-123.cn-hangzhou.oss-vectors.aliyuncs.com"),
])
def test_provider_contract(provider, endpoint):
    config = VectorBucketConfig(
        provider=provider, bucket="mybucket", index="memory384",
        region="cn-hangzhou" if provider == "aliyun" else "us-east-1",
        dimensions=384, endpoint_url=endpoint,
    )
    client = FakeVectors()
    adapter = VectorBucketAdapter(config, client=client)
    vector = [0.1] * 384
    key = adapter.put(tenant_id="t1", memory_id="m1", model_id="e1", embedding=vector)
    assert client.writes[0]["vectors"][0]["data"]["float32"] == vector
    assert client.writes[0]["vectors"][0]["metadata"]["tenant_id"] == "t1"
    client.reply = {"vectors": [
        {"key": key, "metadata": {"tenant_id": "t1", "memory_id": "m1", "model_id": "e1"}, "distance": 0.12},
        {"key": "fake", "metadata": {"tenant_id": "t2", "memory_id": "m2", "model_id": "e1"}},
    ]}
    hits = adapter.query(tenant_id="t1", model_id="e1", embedding=vector)
    assert [hit.memory_id for hit in hits] == ["m1"]
    assert client.queries[0]["filter"] == {"$and": [{"tenant_id": "t1"}, {"model_id": "e1"}]}
    adapter.delete(tenant_id="t1", memory_id="m1", model_id="e1")
    assert client.deletions[0]["keys"] == [key]


def test_rejects_invalid_vectors_and_configuration():
    with pytest.raises(ValueError):
        VectorBucketConfig("aliyun", "bucket", "idx", "cn-hangzhou", 384)
    with pytest.raises(ValueError):
        VectorBucketConfig("aws", "bucket", "idx", "us-east-1", 384, "https://example.com")
    adapter = VectorBucketAdapter(
        VectorBucketConfig("aws", "bucket", "idx", "us-east-1", 384),
        client=FakeVectors(),
    )
    for invalid in ([0.1], [float("nan")] * 384, [True] * 384):
        with pytest.raises(ValueError):
            adapter.put(tenant_id="t", memory_id="m", model_id="e", embedding=invalid)
    with pytest.raises(ValueError):
        adapter.query(tenant_id="t", model_id="e", embedding=[0.1] * 384, top_k=101)


def test_key_prevents_ambiguous_combinations():
    assert VectorBucketAdapter.key_for("a:b", "c", "d") != VectorBucketAdapter.key_for("a", "b:c", "d")


def test_rejects_wrong_model_and_tampered_key():
    fake = FakeVectors()
    adapter = VectorBucketAdapter(
        VectorBucketConfig("aws", "bucket", "idx", "us-east-1", 384),
        client=fake,
    )
    fake.reply = {"vectors": [
        {"key": "wrong", "metadata": {"tenant_id": "t1", "memory_id": "m1", "model_id": "m1"}},
        {"key": "tampered", "metadata": {"tenant_id": "t1", "memory_id": "m1", "model_id": "expected"}},
    ]}
    assert adapter.query(tenant_id="t1", model_id="expected", embedding=[0.1] * 384) == []

def test_provider_name_and_endpoint_compatibility():
    from agent_memory_core.vector_bucket import provider_capabilities
    assert provider_capabilities("aws").enhanced_mode is True
    assert provider_capabilities("aliyun").enhanced_mode is False
    assert provider_capabilities("aliyun").fusion_mode is True
    with pytest.raises(ValueError):
        VectorBucketConfig("aliyun", "mybucket", "index-with-dashes", "cn-hangzhou",
                           384, "https://mybucket-123.cn-hangzhou.oss-vectors.aliyuncs.com")
    with pytest.raises(ValueError):
        VectorBucketConfig("aliyun", "mybucket", "index123", "cn-hangzhou",
                           384, "https://otherbucket-123.cn-hangzhou.oss-vectors.aliyuncs.com")
    with pytest.raises(ValueError):
        VectorBucketConfig("aliyun", "mybucket", "index123", "cn-hangzhou",
                           384, "http://mybucket-123.cn-hangzhou.oss-vectors.aliyuncs.com")
    with pytest.raises(ValueError):
        VectorBucketConfig("aws", "mybucket", "index_name", "us-east-1", 384)
