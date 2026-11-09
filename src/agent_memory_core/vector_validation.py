"""Strict embedding validation, independent of a database session."""
from math import isfinite


def vector_literal(values: list[float], dimension: int | None = None) -> str:
    if len(values) not in (384, 512):
        raise ValueError("Embedding profile must be 384 or 512 dimensions")
    if dimension is not None and len(values) != dimension:
        raise ValueError("Embedding dimension mismatch")
    numbers = [float(v) for v in values]
    if not all(isfinite(x) for x in numbers):
        raise ValueError("Embedding must have finite components")
    if not any(x != 0 for x in numbers):
        raise ValueError("Cosine distance is undefined for a zero vector")
    return "[" + ",".join(format(x, ".17g") for x in numbers) + "]"
