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


def attest_pair(a, b):
    """Give legacy strict-comparison fixtures observed search/run evidence."""
    observed = {"aliases_enabled": False, "alias_count": 0,
                "alias_sha256": "a" * 64}
    for report, run_id in ((a, "run-a"), (b, "run-b")):
        report["search_attestation"] = dict(observed)
        report["run_id"] = run_id



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
                           "model_digest": "sha256-pinned",
                           "case_isolation": "fresh_tenant_per_story",
                           "extractor_runtime_identity": {
                               "verified": True,
                               "response_model":"qwen-local",
                               "model_digest":"sha256-pinned",
                               "system_fingerprint":None,
                               "runtime_version":"local-version",
                               "cryptographic_weight_identity":False},
                           "answer_runtime_identity": {
                               "verified":True,
                               "response_model":"qwen-answer",
                               "system_fingerprint":None,
                               "cryptographic_weight_identity":False}}}
    b = deepcopy(a)
    b["configuration"].update({"name": "w3", "context_window": 3})
    attest_pair(a, b)
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
                           "context_window": context_a,
                           "case_isolation": "fresh_tenant_per_story",
                           "extractor_runtime_identity": {
                               "verified":True,"response_model":"model"},
                           "answer_runtime_identity": {
                               "verified":True,"response_model":"answer"}}}
    b = deepcopy(a)
    b["configuration"].update({"name": "b", "context_window": context_b})
    attest_pair(a, b)
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


def test_strict_context_pair_rejects_unverified_adapter_even_if_names_match():
    a = {"configuration":{
        "name":"w1","extractor":"model","answer_model":"answer",
        "aliases_enabled":False,"context_enabled":True,"context_window":1,
        "case_isolation":"fresh_tenant_per_story",
        "extractor_runtime_identity":{"verified":False,"reason":"unattested"},
    }}
    b = deepcopy(a)
    b["configuration"].update({"name":"w3","context_window":3})
    attest_pair(a, b)
    with pytest.raises(ValueError,match="runtime identity"):
        comparator.require_context_window_pair(a,b)


def test_strict_context_pair_rejects_answer_runtime_swap():
    a = {"configuration":{
        "name":"w1", "extractor":"model", "answer_model":"qwen-answer",
        "aliases_enabled":False, "context_enabled":True, "context_window":1,
        "case_isolation":"fresh_tenant_per_story",
        "extractor_runtime_identity":{"verified":True,"response_model":"model"},
        "answer_runtime_identity":{"verified":True,"response_model":"qwen-answer",
                                   "system_fingerprint":"host-v1"},
    }}
    b = deepcopy(a)
    b["configuration"].update({"name":"w3","context_window":3})
    attest_pair(a, b)
    comparator.require_context_window_pair(a,b)
    b["configuration"]["answer_runtime_identity"]["system_fingerprint"]="host-v2"
    with pytest.raises(ValueError,match="answer runtime identity"):
        comparator.require_context_window_pair(a,b)


def test_strict_real_capture_rejects_shared_tenant_soft_reset():
    _, payload = fixture()
    payload["capture_type"] = "real_http_and_local_model"
    payload["configuration"].update({
        "extractor": "local-model", "answer_model": "answer-model",
        "case_isolation": "shared_tenant_soft_reset",
    })
    payload["search_attestation"] = {"aliases_enabled":False}
    with pytest.raises(ValueError,match="fresh tenant"):
        diagnostics.require_real_capture(payload)
    payload["configuration"]["case_isolation"] = "fresh_tenant_per_story"
    # Merely declaring model names or fresh tenancy no longer suffices.
    with pytest.raises(ValueError, match="observed runtime identity"):
        diagnostics.require_real_capture(payload)
    payload["configuration"].update({
        "extractor_runtime_identity":{"verified":True,"response_model":"local-model"},
        "answer_runtime_identity":{"verified":True,"response_model":"answer-model"},
    })
    payload["search_attestation"]={
        "aliases_enabled":False,"alias_count":0,"alias_sha256":"a"*64}
    payload["isolated_tenant_count"]=len(payload["cases"])
    payload["extractor_runtime_call_count"]=len(payload["cases"])
    payload["answer_runtime_call_count"]=len(payload["cases"])
    diagnostics.require_real_capture(payload)


def test_strict_window_comparison_rejects_shared_case_history():
    a = {"configuration":{
        "name":"one","extractor":"local","answer_model":"answer",
        "context_enabled":True,"context_window":1,"aliases_enabled":False,
        "case_isolation":"shared_tenant_soft_reset",
        "extractor_runtime_identity":{"verified":True,"response_model":"local"},
        "answer_runtime_identity":{"verified":True,"response_model":"answer"},
    }}
    b = deepcopy(a)
    b["configuration"].update({"name":"three","context_window":3})
    attest_pair(a, b)
    with pytest.raises(ValueError,match="fresh tenant"):
        comparator.require_context_window_pair(a,b)


def test_strict_real_capture_refuses_missing_or_inconsistent_model_evidence():
    _, payload = fixture()
    payload["capture_type"] = "real_http_and_local_model"
    payload["configuration"].update({
        "name":"live-capture","extractor":"local-model",
        "answer_model":"answer-model","case_isolation":"fresh_tenant_per_story",
        "extractor_runtime_identity":{"verified":True,"response_model":"local-model"},
        "answer_runtime_identity":{"verified":True,"response_model":"answer-model"},
    })
    payload["search_attestation"]={
        "aliases_enabled":False,"alias_count":0,"alias_sha256":"a"*64}
    n=len(payload["cases"])
    payload.update({"isolated_tenant_count":n,
                    "extractor_runtime_call_count":n,
                    "answer_runtime_call_count":n})
    diagnostics.require_real_capture(payload)
    variants = [
        ("configuration", {"answer_runtime_identity":{"verified":False,"response_model":"answer-model"}}),
        ("configuration", {"extractor_runtime_identity":{"verified":True}}),
        ("configuration", {"extractor_runtime_identity":{"verified":True,"response_model":"wrong-extractor"}}),
        ("configuration", {"answer_runtime_identity":{"verified":True,"response_model":"changed"}}),
        ("configuration", {"aliases_enabled":True}),
        ("search_attestation", {"aliases_enabled":False}),
        ("isolated_tenant_count", n-1),
        ("extractor_runtime_call_count", 0),
        ("answer_runtime_call_count", n-1),
    ]
    for section, value in variants:
        wrong=deepcopy(payload)
        if section == "configuration":
            wrong[section].update(value)
        else:
            wrong[section]=value
        with pytest.raises(ValueError):
            diagnostics.require_real_capture(wrong)
