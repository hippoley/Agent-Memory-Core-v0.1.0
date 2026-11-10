"""Agent Memory Core: local-first memory and third-party Agent SDK.

Optional transport/model adapters are lazily imported to keep Temporal's
deterministic workflow sandbox free of network and HTTP imports.
"""
from .store import MemoryStore, MemoryConflict, MemoryRecord
from .objects import ObjectStore, S3ObjectStore
from .processing import MemoryProcessor, ProcessingReport

__all__ = [
    "MemoryStore", "MemoryConflict", "MemoryRecord",
    "ObjectStore", "S3ObjectStore", "MemoryProcessor", "ProcessingReport",
    "MemoryClient", "MemoryAPIError", "OllamaFactExtractor",
]


def __getattr__(name):
    if name in ("MemoryClient","MemoryAPIError"):
        from . import sdk
        return getattr(sdk,name)
    if name == "OllamaFactExtractor":
        from .local_extractor import OllamaFactExtractor
        return OllamaFactExtractor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
