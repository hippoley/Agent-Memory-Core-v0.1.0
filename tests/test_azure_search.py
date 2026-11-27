"""Offline Azure AI Search contract tests; no Azure credentials or network."""
import pytest
from agent_memory_core.azure_search import AzureSearchAdapter, AzureSearchConfig
from agent_memory_core.vector_bucket import VectorBucketAdapter


class Result:
    def __init__(self, succeeded=True):
        self.succeeded = succeeded


class FakeAzure:
    def __init__(self):
        self.uploads = []
        self.searches = []
        self.deletes = []
        self.rows = []

    def upload_documents(self, **kwargs):
        self.uploads.append(kwargs)
        return [Result()]

    def search(self, **kwargs):
        self.searches.append(kwargs)
        return self.rows

    def delete_documents(self, **kwargs):
        self.deletes.append(kwargs)
        return [Result()]


def make_adapter(fake):
    config = AzureSearchConfig("https://amc-demo.search.windows.net",
                               "memory-index", 384)
    return AzureSearchAdapter(config, client=fake, vector_query_factory=lambda **k: k)


def test_azure_portable_contract_filters_and_validates_ids():
    fake = FakeAzure()
    adapter = make_adapter(fake)
    key = adapter.put(tenant_id="tenant's", memory_id="m1", model_id="embed1", embedding=[0.2]*384)
    assert key == VectorBucketAdapter.key_for("tenant's", "m1", "embed1")
    assert fake.uploads[0]["documents"][0]["embedding"] == [0.2]*384
    fake.rows = [
        {"id": key, "tenant_id": "tenant's", "memory_id": "m1", "model_id": "embed1", "@search.score": 0.8},
        {"id": "wrong", "tenant_id": "tenant's", "memory_id": "m2", "model_id": "embed1"},
        {"id": key, "tenant_id": "other", "memory_id": "m1", "model_id": "embed1"},
    ]
    hits = adapter.query(tenant_id="tenant's", model_id="embed1", embedding=[0.2]*384)
    assert [x.memory_id for x in hits] == ["m1"]
    assert hits[0].distance is None
    assert fake.searches[0]["filter"] == "tenant_id eq 'tenant''s' and model_id eq 'embed1'"
    assert fake.searches[0]["vector_filter_mode"] == "preFilter"
    adapter.delete(tenant_id="tenant's", memory_id="m1", model_id="embed1")
    assert fake.deletes[0]["documents"][0]["id"] == key


def test_bad_azure_endpoints_and_vectors_rejected():
    for endpoint in ("http://amc-demo.search.windows.net",
                     "https://other.example.com", "https://amc-demo.search.windows.net.evil.com",
                     "https://amc-demo.search.windows.net/path"):
        with pytest.raises(ValueError):
            AzureSearchConfig(endpoint, "memory-index", 384)
    fake = FakeAzure()
    adapter = make_adapter(fake)
    for bad in ([0.1], [float("inf")]*384):
        with pytest.raises(ValueError):
            adapter.put(tenant_id="t", memory_id="m", model_id="e", embedding=bad)
    with pytest.raises(ValueError):
        adapter.query(tenant_id="t", model_id="e", embedding=[0.1]*384, top_k=101)


def test_failed_document_index_result_must_raise():
    class Failed(FakeAzure):
        def upload_documents(self, **kwargs):
            return [Result(False)]
    with pytest.raises(RuntimeError, match="indexing failed"):
        make_adapter(Failed()).put(tenant_id="t", memory_id="m", model_id="e", embedding=[0.1]*384)
