"""Strict embedding validation, independent of a database session."""
from math import isfinite


def vector_literal(values: list[float], dimension: int = 384) -> str:
    if len(values) != dimension:
        raise ValueError("Expected 384-dimensional embedding")
    numbers = [float(v) for v in values]
    if not all(isfinite(x) for x in numbers):
        raise ValueError("Embedding must have finite components")
    if not any(x != 0 for x in numbers):
        raise ValueError("Cosine distance is undefined for a zero vector")
    return "[" + ",".join(format(x, ".17g") for x in numbers) + "]"
