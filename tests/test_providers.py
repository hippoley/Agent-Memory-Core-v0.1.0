"""Provider configuration resolution and embedding adapter contract tests."""
import json
import urllib.request

import pytest

from agent_memory_core import providers


class FakeHTTP:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, n): return self.data


def fake_opener(monkeypatch, handler):
    class Stub:
        def open(self, request, timeout):
            return handler(request, timeout)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Stub())


def clear_env(monkeypatch):
    for name in list(dict(get_configured())).keys():
        monkeypatch.delenv(name, raising=False)


def get_configured():
    import os
    return {k: v for k, v in os.environ.items()
            if k.startswith(("MEMORY_EXTRACTOR_", "MEMORY_EMBEDDING_",
                             "MEMORY_PROVIDER_CONFIG", "MEMORY_LOCAL_OLLAMA_",
                             "MEMORY_RULE_", "MEMORY_AUTO_EMBED"))}


BARE_ENV = ("MEMORY_EXTRACTOR_PROVIDER", "MEMORY_EXTRACTOR_MODEL", "MEMORY_EXTRACTOR_BASE_URL",
            "MEMORY_EXTRACTOR_API_KEY", "MEMORY_EXTRACTOR_TIMEOUT", "MEMORY_EXTRACTOR_JSON_MODE",
            "MEMORY_EMBEDDING_PROVIDER", "MEMORY_EMBEDDING_MODEL", "MEMORY_EMBEDDING_BASE_URL",
            "MEMORY_EMBEDDING_API_KEY", "MEMORY_EMBEDDING_DIMENSIONS",
            "MEMORY_EMBEDDING_BATCH_SIZE", "MEMORY_EMBEDDING_TIMEOUT",
            "MEMORY_PROVIDER_CONFIG", "MEMORY_LOCAL_OLLAMA_MODEL",
            "MEMORY_LOCAL_OLLAMA_ENDPOINT", "MEMORY_AUTO_EMBED")


@pytest.fixture(autouse=True)
def clean_provider_env(monkeypatch):
    for name in BARE_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


def test_no_provider_configured_returns_none():
    assert providers.provider_settings("extractor") is None
    assert providers.provider_settings("embedding") is None
    assert providers.build_extractor() is None
    assert providers.build_embedder() is None


def test_deepseek_preset_supplies_endpoint_and_model(monkeypatch):
    monkeypatch.setenv("MEMORY_EXTRACTOR_PROVIDER", "deepseek")
    monkeypatch.setenv("MEMORY_EXTRACTOR_API_KEY", "sk-test")
    settings = providers.provider_settings("extractor")
    assert settings["base_url"] == "https://api.deepseek.com/v1/chat/completions"
    assert settings["model"] == "deepseek-chat"
    assert settings["api_key"] == "sk-test"
    extractor = providers.build_extractor("tenant-a")
    from agent_memory_core.openai_compat import OpenAICompatFactExtractor
    assert isinstance(extractor, OpenAICompatFactExtractor)


def test_selfhosted_preset_works_without_key(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_PROVIDER", "vllm")
    monkeypatch.setenv("MEMORY_EMBEDDING_MODEL", "bge-m3")
    settings = providers.provider_settings("embedding")
    assert settings["base_url"] == "http://127.0.0.1:8000/v1/embeddings"
    assert settings["api_key"] is None
    assert providers.build_embedder() is not None


def test_unknown_provider_is_rejected_with_actionable_message(monkeypatch):
    monkeypatch.setenv("MEMORY_EXTRACTOR_PROVIDER", "anthropic-ish")
    with pytest.raises(RuntimeError, match="Unknown provider"):
        providers.provider_settings("extractor")


def test_preset_without_embedding_endpoint_requires_explicit_url(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_PROVIDER", "deepseek")
    with pytest.raises(RuntimeError, match="no embedding endpoint preset"):
        providers.provider_settings("embedding")


def test_missing_model_requires_explicit_model(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_PROVIDER", "vllm")
    with pytest.raises(RuntimeError, match="no default embedding model"):
        providers.provider_settings("embedding")


def test_unsupported_dimensions_are_rejected(monkeypatch):
    monkeypatch.setenv("MEMORY_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("MEMORY_EMBEDDING_DIMENSIONS", "1536")
    with pytest.raises(RuntimeError, match="vector\\(384\\)"):
        providers.provider_settings("embedding")


def test_legacy_ollama_model_env_still_selects_native_adapter(monkeypatch):
    monkeypatch.setenv("MEMORY_LOCAL_OLLAMA_MODEL", "qwen2.5:7b")
    settings = providers.provider_settings("extractor")
    assert settings["provider"] == "ollama-native"
    extractor = providers.build_extractor("t")
    from agent_memory_core.local_extractor import OllamaFactExtractor
    assert isinstance(extractor, OllamaFactExtractor)


def test_config_file_is_overridden_by_environment(monkeypatch, tmp_path):
    config = tmp_path / "providers.json"
    config.write_text(json.dumps({
        "extractor": {"provider": "dashscope", "model": "qwen-max",
                      "api_key": "file-key"},
    }), encoding="utf-8")
    monkeypatch.setenv("MEMORY_PROVIDER_CONFIG", str(config))
    settings = providers.provider_settings("extractor")
    assert settings["model"] == "qwen-max"
    assert settings["api_key"] == "file-key"
    monkeypatch.setenv("MEMORY_EXTRACTOR_MODEL", "qwen-plus")
    assert providers.provider_settings("extractor")["model"] == "qwen-plus"


def test_provider_status_never_leaks_keys(monkeypatch):
    monkeypatch.setenv("MEMORY_EXTRACTOR_PROVIDER", "openai")
    monkeypatch.setenv("MEMORY_EXTRACTOR_API_KEY", "sk-secret-should-not-appear")
    status = providers.provider_status()
    assert status["extractor"]["configured"] is True
    assert status["extractor"]["authenticated"] is True
    assert "sk-secret-should-not-appear" not in json.dumps(status)
    assert status["embedding"]["configured"] is False


def test_claim_embedding_text_is_stable_and_readable():
    candidate = {"subject_id": "bedroom", "predicate": "noise_preference",
                 "value": {"level": "quiet", "temperature": 22}}
    text = providers.claim_embedding_text(candidate)
    assert text == 'bedroom noise_preference {"level":"quiet","temperature":22}'
    assert providers.claim_embedding_text({"subject_name": "主卧", "predicate": "light",
                                           "value": {"level": "dim"}}) \
        == '主卧 light {"level":"dim"}'


def test_embedder_call_returns_model_id_and_vector(monkeypatch):
    def serve(request, timeout):
        payload = json.loads(request.data)
        assert payload["model"] == "text-embedding-3-small"
        assert payload["dimensions"] == 512
        data = [{"index": i, "embedding": [0.01] * 512} for i in range(len(payload["input"]))]
        return FakeHTTP(json.dumps({"data": data}).encode())
    fake_opener(monkeypatch, serve)
    from agent_memory_core.openai_compat import OpenAICompatEmbedder
    from agent_memory_core.providers import ProviderEmbedder
    embedder = ProviderEmbedder(
        OpenAICompatEmbedder(model="text-embedding-3-small",
                             base_url="https://api.openai.com/v1/embeddings",
                             api_key="sk-x", dimensions=512),
        model_id="text-embedding-3-small")
    model_id, vector = embedder({"subject_id": "bedroom", "predicate": "noise",
                                 "value": {"level": "quiet"}})
    assert model_id == "text-embedding-3-small"
    assert len(vector) == 512
    assert embedder.embed_text("quiet bedroom") == [0.01] * 512


def test_embedder_rejects_unsupported_dimensions_from_provider(monkeypatch):
    def serve(request, timeout):
        return FakeHTTP(json.dumps({"data": [
            {"index": 0, "embedding": [0.01] * 1024}]}).encode())
    fake_opener(monkeypatch, serve)
    from agent_memory_core.openai_compat import OpenAICompatEmbedder
    embedder = OpenAICompatEmbedder(model="m", base_url="https://x.example/v1/embeddings")
    with pytest.raises(RuntimeError, match="vector\\(384\\)"):
        embedder.embed(["hello"])


def test_embedder_rejects_inconsistent_and_nonfinite_vectors(monkeypatch):
    from agent_memory_core.openai_compat import OpenAICompatEmbedder

    def ragged(request, timeout):
        return FakeHTTP(json.dumps({"data": [
            {"index": 0, "embedding": [0.1] * 384},
            {"index": 1, "embedding": [0.1] * 512}]}).encode())
    fake_opener(monkeypatch, ragged)
    embedder = OpenAICompatEmbedder(model="m", base_url="https://x.example/v1/embeddings")
    with pytest.raises(RuntimeError, match="inconsistent dimensions"):
        embedder.embed(["a", "b"])

    def nonfinite(request, timeout):
        vector = [0.1] * 384
        vector[3] = float("inf")
        return FakeHTTP(json.dumps({"data": [{"index": 0, "embedding": vector}]}).encode())
    fake_opener(monkeypatch, nonfinite)
    embedder = OpenAICompatEmbedder(model="m", base_url="https://x.example/v1/embeddings")
    with pytest.raises(RuntimeError, match="non-finite"):
        embedder.embed(["a"])


def test_embedder_batches_requests(monkeypatch):
    seen = []
    def serve(request, timeout):
        payload = json.loads(request.data)
        seen.append(len(payload["input"]))
        data = [{"index": i, "embedding": [0.02] * 384}
                for i in range(len(payload["input"]))]
        return FakeHTTP(json.dumps({"data": data}).encode())
    fake_opener(monkeypatch, serve)
    from agent_memory_core.openai_compat import OpenAICompatEmbedder
    embedder = OpenAICompatEmbedder(model="m", base_url="https://x.example/v1/embeddings",
                                    batch_size=2)
    vectors = embedder.embed(["a", "b", "c", "d", "e"])
    assert len(vectors) == 5
    assert seen == [2, 2, 1]


def test_embedder_rejects_bad_base_urls():
    from agent_memory_core.openai_compat import OpenAICompatEmbedder
    for url in ["https://x.example/v1/chat/completions", "https://x.example/",
                "ftp://x.example/v1/embeddings", "https://u:p@x.example/v1/embeddings"]:
        with pytest.raises(ValueError):
            OpenAICompatEmbedder(model="m", base_url=url)
