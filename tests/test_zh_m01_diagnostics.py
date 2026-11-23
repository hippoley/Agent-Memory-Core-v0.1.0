"""Regression gates for M01 error diagnosis and fair 1-vs-3 context ablation."""
from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scorer = load("scorer_m01", "scripts/eval/score_zh_frozen.py")
diagnostics = load("diagnostics_m01", "scripts/eval/diagnose_zh_frozen.py")
comparator = load("comparator_m01", "scripts/eval/compare_zh_frozen.py")


def fixture():
    gold, digest = scorer.read_gold(scorer.DEFAULT_GOLD)
    cases = [{
        "id": case["id"],
        "extracted": deepcopy(case["expected_events"]),
        "current": deepcopy(case["expected_current"]),
        "retrieved": deepcopy(case["expected_retrieval"]),
        "answer": case["accepted_answers"][0],
    } for case in gold]
    payload = {
        "dataset_sha256": digest,
        "configuration": {
            "name": "oracle-fixture-not-a-model", "extractor": "fixture",
            "context_enabled": True, "context_window": 1,
            "aliases_enabled": False, "answer_model": "fixture",
        },
        "cases": cases,
    }
    return gold, payload


def test_diagnostics_perfect_oracle_is_only_test_fixture():
    gold, payload = fixture()
    report = diagnostics.diagnose(gold, {x["id"]: x for x in payload["cases"]})
    assert report["failed_cases"] == 0
    assert not report["failures"]
    assert set(report["failures_by_stage"]) == set(scorer.STAGES)


def test_diagnostics_identifies_first_error_and_independent_stages():
    gold, payload = fixture()
    case = next(x for x in payload["cases"] if x["id"] == "zh01")
    case["extracted"].clear()
    case["current"] = [{"subject_id": "主卧", "predicate": "temperature_preference",
                        "value": {"celsius": 24}}]
    case["retrieved"] = [{"subject_id": "主卧", "predicate": "temperature_preference",
                          "value": {"celsius": 24}}]
    case["answer"] = "24度"
    report = diagnostics.diagnose(gold, {x["id"]: x for x in payload["cases"]})
    assert report["failed_cases"] == 1
    failure = report["failures"][0]
    assert failure["id"] == "zh01"
    assert failure["first_failure_stage"] == "extracted"
    assert failure["failed_stages"] == list(scorer.STAGES)
    assert failure["issues"]["extracted"]["missing"]
    assert failure["issues"]["current"]["unexpected"]
    assert failure["issues"]["retrieved"]["missing"]
    assert failure["issues"]["answer"]["actual"] == "24度"


def test_diagnostics_preserves_negative_query_and_rejects_unjustified_memory():
    gold, payload = fixture()
    case = next(x for x in payload["cases"] if x["id"] == "zh04")
    case["retrieved"] = [{"subject_id": "书房", "predicate": "noise_preference",
                          "value": {"level": "quiet"}}]
    report = diagnostics.diagnose(gold, {x["id"]: x for x in payload["cases"]})
    failure = next(x for x in report["failures"] if x["id"] == "zh04")
    assert failure["issues"]["retrieved"]["unexpected"]


def test_context_pair_same_models_and_only_window_changed():
    a = {"configuration": {"name": "w1", "extractor": "qwen-local",
                           "context_enabled": True, "context_window": 1,
                           "aliases_enabled": False, "answer_model": "qwen-answer",
                           "model_digest": "sha256-pinned"}}
    b = deepcopy(a)
    b["configuration"].update({"name": "w3", "context_window": 3})
    comparator.require_context_window_pair(a, b)
    for field, value in (("extractor", "another-model"),
                         ("answer_model", "another-answer"),
                         ("aliases_enabled", True),
                         ("model_digest", "another-sha")):
        invalid = deepcopy(b)
        invalid["configuration"][field] = value
        with pytest.raises(ValueError):
            comparator.require_context_window_pair(a, invalid)


@pytest.mark.parametrize("context_a,context_b", [(0,3), (1,2), (3,1), (1,4)])
def test_context_pair_rejects_incorrect_or_incomparable_windows(context_a, context_b):
    a = {"configuration": {"name": "a", "extractor": "model", "answer_model": "answer",
                           "aliases_enabled": False, "context_enabled": True,
                           "context_window": context_a}}
    b = deepcopy(a)
    b["configuration"].update({"name": "b", "context_window": context_b})
    with pytest.raises(ValueError, match="window"):
        comparator.require_context_window_pair(a, b)


def test_fixture_cannot_pass_real_capture_gate(tmp_path):
    gold, payload = fixture()
    pred = tmp_path / "predictions.json"
    import json
    pred.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="Real capture"):
        # Call the very same strict attestation predicate as the CLI gate.
        diagnostics.require_real_capture(payload)
