"""Small, deterministic scoring helpers for structured memory extraction."""
from __future__ import annotations

import json
from collections import Counter


def candidate_key(candidate: dict, *, include_polarity: bool = True) -> tuple:
    subject = candidate.get("subject_id") or candidate.get("subject_name")
    value = json.dumps(candidate.get("value"), ensure_ascii=False,
                        sort_keys=True, separators=(",", ":"))
    base = (subject, candidate.get("predicate"), value)
    return base + ((candidate.get("polarity", "positive"),) if include_polarity else ())


def score_case(expected: list[dict], predicted: list[dict]) -> dict:
    expected_counts = Counter(candidate_key(x) for x in expected)
    predicted_counts = Counter(candidate_key(x) for x in predicted)
    true_positive = sum((expected_counts & predicted_counts).values())
    false_positive = sum((predicted_counts - expected_counts).values())
    false_negative = sum((expected_counts - predicted_counts).values())
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else (1.0 if not expected else 0.0)
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    expected_base = Counter(candidate_key(x, include_polarity=False) for x in expected)
    predicted_base = Counter(candidate_key(x, include_polarity=False) for x in predicted)
    comparable = sum((expected_base & predicted_base).values())
    polarity_correct = 0
    remaining = expected_base.copy()
    expected_polarities = {}
    for candidate in expected:
        expected_polarities.setdefault(candidate_key(candidate, include_polarity=False), []).append(candidate.get("polarity", "positive"))
    seen = {}
    for candidate in predicted:
        key = candidate_key(candidate, include_polarity=False)
        index = seen.get(key, 0)
        choices = expected_polarities.get(key, [])
        if index < len(choices) and choices[index] == candidate.get("polarity", "positive"):
            polarity_correct += 1
        seen[key] = index + 1
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "polarity_accuracy": polarity_correct / comparable if comparable else None,
        "polarity_comparable": comparable,
        "expected_count": len(expected),
        "predicted_count": len(predicted),
    }
