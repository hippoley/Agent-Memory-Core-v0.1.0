"""Reusable benchmark metric helpers."""
from __future__ import annotations

import math


def percentile(values, p):
    if not 0 <= p <= 1:
        raise ValueError("p must be between 0 and 1")
    if not values:
        return None
    ordered=sorted(values)
    index=max(0,min(len(ordered)-1,math.ceil(p*len(ordered))-1))
    return ordered[index]
