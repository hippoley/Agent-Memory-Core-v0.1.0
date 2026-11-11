"""Auto-embedding on write and search: provider-backed vectors without caller work."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_memory_core.api import create_app

KEY = "secret-token-with-more-than-24-characters"
HEADERS = {"Authorization": f"Bearer {KEY}"}


class FakeDB:
    def __init__(self):
        self.memories = []
        self.embeddings = []

    def remember(self, **kwargs):
        mid = f"memory-{len(self.memories) + 1}"
        self.memories.append({"id": mid, **kwargs})
        return mid

    def recall(self, *, tenant_id, subject_id, predicate=None, valid_at=None,
               known_at=None):
        return [row for row in self.memories
                if row["tenant_id"] == tenant_id and row["subject_id"] == subject_id]

    def store_embedding(self, *, tenant_id, memory_id, model_id, vector):
        self.embeddings.append({"tenant_id": tenant_id, "memory_id": memory_id,
                                "model_id": model_id, "dimensions": len(vector)})

    def close(self):
        pass


class FakeEmbedder:
    model_id = "bge-m3"

    def __init__(self):
        self.texts = []

    def embed_text(self, text):
        self.texts.append(text)
        return [0.01] * 512

    def __call__(self, candidate):
        self.texts.append(candidate)
        return self.model_id, [0.01] * 512


def app_with(db, embedder, *, auto_embed=True, search=None, flags=None):
    return create_app(api_key=KEY, tenant_id="home", store_factory=lambda: db,
                      embedder=embedder, auto_embed=auto_embed,
                      search_factory=(lambda: search) if search is not None else None,
                      capability_flags=flags)


def test_write_auto_embeds_when_provider_configured():
    db = FakeDB()
    embedder = FakeEmbedder()
    client = TestClient(app_with(db, embedder))
    response = client.post("/v1/memories", headers=HEADERS, json={
        "subject_id": "bedroom", "predicate": "noise_preference",
        "value": {"level": "quiet"}, "source_id": "user:1",
        "valid_from": "2026-11-11T00:00:00Z"})
    assert response.status_code == 201, response.text
    assert response.json()["embedded"] is True
    assert db.embeddings == [{"tenant_id": "home", "memory_id": "memory-1",
                              "model_id": "bge-m3", "dimensions": 512}]
    assert embedder.texts[0]["predicate"] == "noise_preference"


def test_write_reports_false_but_succeeds_when_provider_fails():
    db = FakeDB()

    class BrokenEmbedder(FakeEmbedder):
        def __call__(self, candidate):
            raise RuntimeError("provider down")

    client = TestClient(app_with(db, BrokenEmbedder()))
    response = client.post("/v1/memories", headers=HEADERS, json={
        "subject_id": "bedroom", "predicate": "noise", "value": {"level": "quiet"},
        "source_id": "user:1", "valid_from": "2026-11-11T00:00:00Z"})
    assert response.status_code == 201
    assert response.json()["embedded"] is False
    assert db.memories  # canonical claim still committed
    assert db.embeddings == []


def test_no_embedding_field_without_provider():
    db = FakeDB()
    client = TestClient(app_with(db, None))
    response = client.post("/v1/memories", headers=HEADERS, json={
        "subject_id": "bedroom", "predicate": "noise", "value": {"level": "quiet"},
        "source_id": "user:1", "valid_from": "2026-11-11T00:00:00Z"})
    assert response.status_code == 201
    assert "embedded" not in response.json()
    assert db.embeddings == []


def test_auto_embed_can_be_disabled_by_configuration():
    db = FakeDB()
    embedder = FakeEmbedder()
    client = TestClient(app_with(db, embedder, auto_embed=False))
    response = client.post("/v1/memories", headers=HEADERS, json={
        "subject_id": "bedroom", "predicate": "noise", "value": {"level": "quiet"},
        "source_id": "user:1", "valid_from": "2026-11-11T00:00:00Z"})
    assert response.status_code == 201
    assert "embedded" not in response.json()
    assert db.embeddings == []


class FakeSearch:
    def __init__(self):
        self.calls = []

    def search_detailed(self, **kwargs):
        self.calls.append(kwargs)
        from types import SimpleNamespace
        return SimpleNamespace(hits=[], channels_used=("sql",), degraded_channels={})

    def close(self):
        pass


def test_search_auto_embeds_query_when_semantic_enabled():
    search = FakeSearch()
    embedder = FakeEmbedder()
    client = TestClient(app_with(FakeDB(), embedder, search=search,
                                 flags={"semantic": True, "graph": False}))
    response = client.post("/v1/search", headers=HEADERS, json={"query": "安静 卧室"})
    assert response.status_code == 200, response.text
    call = search.calls[0]
    assert call["embedding"] == [0.01] * 512
    assert call["model_id"] == "bge-m3"
    assert embedder.texts == ["安静 卧室"]


def test_search_keeps_caller_embedding_unchanged():
    search = FakeSearch()
    embedder = FakeEmbedder()
    client = TestClient(app_with(FakeDB(), embedder, search=search,
                                 flags={"semantic": True, "graph": False}))
    caller_vector = [0.5] * 384
    response = client.post("/v1/search", headers=HEADERS, json={
        "query": "quiet", "embedding": caller_vector, "model_id": "caller-model"})
    assert response.status_code == 200
    assert search.calls[0]["embedding"] == caller_vector
    assert search.calls[0]["model_id"] == "caller-model"
    assert embedder.texts == []


def test_search_degrades_to_lexical_when_provider_fails():
    search = FakeSearch()

    class BrokenEmbedder(FakeEmbedder):
        def embed_text(self, text):
            raise RuntimeError("provider down")

    client = TestClient(app_with(FakeDB(), BrokenEmbedder(), search=search,
                                 flags={"semantic": True, "graph": False}))
    response = client.post("/v1/search", headers=HEADERS, json={"query": "quiet"})
    assert response.status_code == 200
    assert search.calls[0]["embedding"] is None
    assert search.calls[0]["model_id"] is None


def test_process_uses_embedder_for_extracted_candidates():
    from agent_memory_core.processing import MemoryProcessor

    class ProcessorDB(FakeDB):
        def link_claim_source(self, **kwargs):
            return True

        def resolve_entity(self, *, tenant_id, name):
            return None

    db = ProcessorDB()
    embedder = FakeEmbedder()
    processor = MemoryProcessor(db, embedder=embedder)
    report = processor.process(
        tenant_id="home", text="卧室保持安静", source_id="s:1",
        extractor=lambda text: [{"subject_id": "bedroom",
                                 "predicate": "noise_preference",
                                 "value": {"level": "quiet"}, "confidence": 0.9}])
    assert report.created
    assert report.embedding_failures == []
    assert db.embeddings and db.embeddings[0]["model_id"] == "bge-m3"
