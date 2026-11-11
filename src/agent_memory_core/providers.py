"""First-class model provider configuration.

One place resolves the operator's extraction and embedding providers from a
preset name, environment overrides, and an optional JSON config file. Presets
only supply defaults (endpoint + model); credentials always come from the
environment or the config file and are never logged.

Why presets: the memory core stays deterministic, but the two places where a
model genuinely adds quality (free-text extraction, semantic vectors) must not
silently disappear. When no provider is configured the capability is reported
as unavailable by ``/v1/capabilities`` instead of failing halfway through a
write.

Configuration (extractor / embedding)

    MEMORY_EXTRACTOR_PROVIDER   preset name: openai|deepseek|dashscope|zhipu|
                                ollama|vllm|custom, or legacy: ollama-native
    MEMORY_EXTRACTOR_MODEL      model id (preset default otherwise)
    MEMORY_EXTRACTOR_BASE_URL   full .../chat/completions URL
    MEMORY_EXTRACTOR_API_KEY    bearer token (optional for self-hosted)
    MEMORY_EXTRACTOR_TIMEOUT    seconds, default 30
    MEMORY_EXTRACTOR_JSON_MODE  1|0, default 1 (response_format json_object)

    MEMORY_EMBEDDING_PROVIDER   same preset names
    MEMORY_EMBEDDING_MODEL      model id (preset default otherwise)
    MEMORY_EMBEDDING_BASE_URL   full .../embeddings URL
    MEMORY_EMBEDDING_API_KEY    bearer token
    MEMORY_EMBEDDING_DIMENSIONS requested dimensions (384 or 512; the store
                                only has vector(384)/vector(512) tables)
    MEMORY_EMBEDDING_BATCH_SIZE default 32
    MEMORY_EMBEDDING_TIMEOUT    seconds, default 30

    MEMORY_PROVIDER_CONFIG      path to a JSON file, shape:
                                {"extractor": {...}, "embedding": {...}}
                                Environment variables override file values.

    MEMORY_AUTO_EMBED           1|0, default 1 when an embedding provider exists

Legacy local-only Ollama (native /api/generate, loopback enforced) stays
available as ``ollama-native`` and remains the default when only
``MEMORY_LOCAL_OLLAMA_MODEL`` is set.
"""
from __future__ import annotations

import json
import os

SUPPORTED_DIMENSIONS = (384, 512)

PRESETS: dict[str, dict[str, tuple[str, str | None]]] = {
    "openai": {
        "chat": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
        "embedding": ("https://api.openai.com/v1/embeddings", "text-embedding-3-small"),
    },
    "deepseek": {
        "chat": ("https://api.deepseek.com/v1/chat/completions", "deepseek-chat"),
    },
    "dashscope": {
        "chat": ("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen-plus"),
        "embedding": ("https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings", "text-embedding-v3"),
    },
    "zhipu": {
        "chat": ("https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-plus"),
        "embedding": ("https://open.bigmodel.cn/api/paas/v4/embeddings", "embedding-3"),
    },
    "ollama": {
        "chat": ("http://127.0.0.1:11434/v1/chat/completions", "qwen2.5:7b"),
        "embedding": ("http://127.0.0.1:11434/v1/embeddings", "bge-m3"),
    },
    "ollama-openai": {
        "chat": ("http://127.0.0.1:11434/v1/chat/completions", "qwen2.5:7b"),
    },
    "vllm": {
        "chat": ("http://127.0.0.1:8000/v1/chat/completions", None),
        "embedding": ("http://127.0.0.1:8000/v1/embeddings", None),
    },
}

_ENV_PREFIX = {"extractor": "MEMORY_EXTRACTOR_", "embedding": "MEMORY_EMBEDDING_"}


def _config_file() -> dict:
    path = os.getenv("MEMORY_PROVIDER_CONFIG")
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RuntimeError("MEMORY_PROVIDER_CONFIG must contain a JSON object")
    return data


def provider_settings(kind: str) -> dict | None:
    """Resolve one provider section, or None when the operator disabled it."""
    if kind not in _ENV_PREFIX:
        raise ValueError("kind must be 'extractor' or 'embedding'")
    file_section = _config_file().get(kind) or {}
    if not isinstance(file_section, dict):
        raise RuntimeError(f"MEMORY_PROVIDER_CONFIG {kind} section must be an object")
    prefix = _ENV_PREFIX[kind]

    def read(name, default=None):
        env_value = os.getenv(prefix + name)
        if env_value is not None and env_value.strip() != "":
            return env_value.strip()
        file_value = file_section.get(name.lower())
        if file_value is not None and str(file_value).strip() != "":
            return str(file_value).strip()
        return default

    provider = read("PROVIDER")
    if provider is None and kind == "extractor" and os.getenv("MEMORY_LOCAL_OLLAMA_MODEL"):
        provider = "ollama-native"
    if provider is None:
        return None
    provider = provider.lower()
    if kind == "extractor" and provider == "ollama":
        # Backwards compatibility: provider name "ollama" has always meant the
        # loopback-only native /api/generate adapter. The OpenAI-compatible
        # Ollama endpoint is "ollama-openai".
        provider = "ollama-native"

    if provider == "ollama-native":
        if kind != "extractor":
            raise RuntimeError("ollama-native is only valid for extraction")
        model = read("MODEL") or os.getenv("MEMORY_LOCAL_OLLAMA_MODEL")
        if not model:
            raise RuntimeError(
                "Provider 'ollama-native' requires MEMORY_LOCAL_OLLAMA_MODEL "
                "or MEMORY_EXTRACTOR_MODEL")
        return {
            "provider": provider,
            "model": model,
            "base_url": read("BASE_URL") or os.getenv(
                "MEMORY_LOCAL_OLLAMA_ENDPOINT", "http://127.0.0.1:11434/api/generate"),
            "api_key": None,
            "timeout": float(read("TIMEOUT", "20")),
            "json_mode": True,
            "dimensions": None,
            "batch_size": None,
        }

    preset = PRESETS.get(provider)
    if preset is None and provider not in ("custom", "openai-compat"):
        raise RuntimeError(
            f"Unknown provider '{provider}'. Use one of {sorted(PRESETS)}, "
            "'custom' with an explicit base_url, or 'ollama-native'.")
    kind_key = "chat" if kind == "extractor" else "embedding"
    preset_entry = (preset or {}).get(kind_key, (None, None))
    base_url = read("BASE_URL") or preset_entry[0]
    model = read("MODEL") or preset_entry[1]
    if not base_url:
        raise RuntimeError(
            f"Provider '{provider}' has no {kind} endpoint preset; set "
            f"{prefix}BASE_URL explicitly.")
    if not model:
        raise RuntimeError(
            f"Provider '{provider}' has no default {kind} model; set "
            f"{prefix}MODEL explicitly.")

    settings = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": read("API_KEY"),
        "timeout": float(read("TIMEOUT", "30")),
        "json_mode": read("JSON_MODE", "1") == "1",
        "dimensions": None,
        "batch_size": None,
    }
    if kind == "embedding":
        raw_dimensions = read("DIMENSIONS")
        if raw_dimensions is not None:
            dimensions = int(raw_dimensions)
            if dimensions not in SUPPORTED_DIMENSIONS:
                raise RuntimeError(
                    f"Embedding dimensions must be one of {SUPPORTED_DIMENSIONS}; "
                    "the store has vector(384) and vector(512) tables only.")
            settings["dimensions"] = dimensions
        settings["batch_size"] = int(read("BATCH_SIZE", "32"))
    return settings


def build_extractor(tenant_id: str | None = None):
    """Return the configured extractor callable, or None when unavailable."""
    settings = provider_settings("extractor")
    if settings is None:
        return None
    if settings["provider"] == "ollama-native":
        from .local_extractor import OllamaFactExtractor
        return OllamaFactExtractor(model=settings["model"], endpoint=settings["base_url"],
                                   timeout=settings["timeout"])
    from .openai_compat import OpenAICompatFactExtractor
    return OpenAICompatFactExtractor(
        model=settings["model"], base_url=settings["base_url"],
        api_key=settings["api_key"], timeout=settings["timeout"],
        json_mode=settings["json_mode"])


class ProviderEmbedder:
    """Embeds claim candidates for writes and raw text for queries."""

    def __init__(self, client, *, model_id: str):
        self.client = client
        self.model_id = model_id

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.client.embed(texts)

    def embed_text(self, text: str) -> list[float]:
        return self.client.embed([text])[0]

    def __call__(self, candidate: dict) -> tuple[str, list[float]]:
        text = claim_embedding_text(candidate)
        return self.model_id, self.embed_text(text)


def claim_embedding_text(candidate: dict) -> str:
    """Stable text rendering of a claim used as embedding input."""
    subject = candidate.get("subject_id") or candidate.get("subject_name") or ""
    predicate = candidate.get("predicate") or ""
    value = candidate.get("value")
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) if value is not None else ""
    return f"{subject} {predicate} {rendered}".strip()


def build_embedder() -> ProviderEmbedder | None:
    """Return the configured embedder, or None when unavailable."""
    settings = provider_settings("embedding")
    if settings is None:
        return None
    from .openai_compat import OpenAICompatEmbedder
    client = OpenAICompatEmbedder(
        model=settings["model"], base_url=settings["base_url"],
        api_key=settings["api_key"], timeout=settings["timeout"],
        dimensions=settings["dimensions"], batch_size=settings["batch_size"] or 32)
    return ProviderEmbedder(client, model_id=settings["model"])


def provider_status() -> dict:
    """Human-readable provider summary for startup logs and diagnostics."""
    status = {}
    for kind in ("extractor", "embedding"):
        try:
            settings = provider_settings(kind)
        except Exception as exc:
            status[kind] = {"configured": False, "error": str(exc)[:240]}
            continue
        if settings is None:
            status[kind] = {"configured": False}
            continue
        status[kind] = {
            "configured": True,
            "provider": settings["provider"],
            "model": settings["model"],
            "base_url": settings["base_url"],
            "authenticated": bool(settings["api_key"]),
            "dimensions": settings.get("dimensions"),
        }
    return status
