"""Offline-only local embedding model loader.

The caller must point at an already-downloaded model directory. The loader
never accepts a model hub identifier and always disables remote code and
network downloads.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable


def local_sentence_transformer(model_path: str) -> Callable[[str], list[float]]:
    """Load a SentenceTransformer strictly from an existing local directory."""
    path=Path(model_path).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("Embedding model must be an existing local directory")
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "Install offline embedding support: pip install agent-memory-core[embedding]"
        ) from exc
    model=SentenceTransformer(
        str(path),trust_remote_code=False,local_files_only=True)

    def encode(text: str) -> list[float]:
        if not isinstance(text,str) or not text.strip():
            raise ValueError("Embedding input must be non-empty text")
        output=model.encode([text],normalize_embeddings=True,convert_to_numpy=True)
        return [float(value) for value in output[0]]

    return encode
