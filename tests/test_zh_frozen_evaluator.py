"""Tests for non-LLM frozen Chinese four-stage evaluation semantics."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("zh_scorer", ROOT/"scripts/eval/score_zh_frozen.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _perfect_payload():
    gold, sha = MODULE.read_gold(MODULE.DEFAULT_GOLD)
    predictions = []
    for case in gold:
        predictions.append({
            "id":case["id"],
            "extracted":case["expected_events"],
            "current":case["expected_current"],
            "retrieved":case["expected_retrieval"],
            "answer":case["accepted_answers"][0],
        })
    payload = {
        "dataset_sha256":sha,
        "configuration":{
            "name":"scorer-fixture-not-a-model",
            "extractor":"fixture",
            "context_enabled":True,
            "aliases_enabled":True,
            "answer_model":"fixture",
        },
        "cases":predictions,
    }
    return gold,sha,payload


def test_frozen_corpus_contract_and_unique_ids():
    gold,sha,payload = _perfect_payload()
    assert len(gold)==12
    assert len({x["id"] for x in gold})==12
    assert len(sha)==64
    assert sum(x["expected_abstain"] for x in gold if x.get("expected_abstain"))==3
    assert set(MODULE.STAGES)=={"extracted","current","retrieved","answer"}


def test_perfect_oracle_fixture_scoring_is_one_but_not_model_evidence():
    gold,sha,payload = _perfect_payload()
    preds=MODULE.validate_predictions(payload,gold,sha)
    report=MODULE.score(gold,preds,payload["configuration"],sha)
    assert report["extraction"]["f1"]==1.0
    assert report["revision"]["exact_final_state_accuracy"]==1.0
    assert report["retrieval"]["recall_at_5"]==1.0
    assert report["answer"]["exact_accepted_accuracy"]==1.0
    assert report["answer"]["abstention_accuracy"]==1.0
    assert report["retrieval"]["negative_queries"]>0


def test_no_missing_stages_or_missing_cases_can_pass():
    gold,sha,payload=_perfect_payload()
    payload["cases"][0].pop("answer")
    with pytest.raises(ValueError,match="missing stage"):
        MODULE.validate_predictions(payload,gold,sha)
    _,_,payload=_perfect_payload()
    payload["cases"].pop()
    with pytest.raises(ValueError,match="missing"):
        MODULE.validate_predictions(payload,gold,sha)


def test_wrong_dataset_hash_and_duplicate_cases_are_rejected():
    gold,sha,payload=_perfect_payload()
    payload["dataset_sha256"]="0"*64
    with pytest.raises(ValueError,match="SHA-256"):
        MODULE.validate_predictions(payload,gold,sha)
    _,_,payload=_perfect_payload()
    payload["cases"].append(payload["cases"][0])
    with pytest.raises(ValueError,match="Duplicate"):
        MODULE.validate_predictions(payload,gold,sha)


def test_recall_requires_exact_predicate_and_value_not_just_room():
    gold,sha,payload=_perfect_payload()
    pred=next(x for x in payload["cases"] if x["id"]=="zh06")
    pred["retrieved"]=[{"subject_id":"主卧","predicate":"light_preference","value":{"level":"dim"}}]
    report=MODULE.score(gold,MODULE.validate_predictions(payload,gold,sha),
                        payload["configuration"],sha)
    scored=next(x for x in report["details"] if x["id"]=="zh06")
    assert scored["retrieval_hits"]==0
    assert report["retrieval"]["recall_at_5"]<1


def test_empty_gold_retrieval_is_not_reported_as_perfect_recall():
    gold,sha,payload=_perfect_payload()
    pred=next(x for x in payload["cases"] if x["id"]=="zh04")
    pred["retrieved"]=[{"subject_id":"书房","predicate":"noise_preference","value":{"level":"quiet"}}]
    report=MODULE.score(gold,MODULE.validate_predictions(payload,gold,sha),
                        payload["configuration"],sha)
    assert report["retrieval"]["clean_negative_queries"]<report["retrieval"]["negative_queries"]
    assert report["retrieval"]["precision_recall"]["fp"]>0


def test_retract_compares_scope_not_arbitrary_marker_value():
    expected=[{"turn":1,"subject_id":"次卧","predicate":"noise_preference",
               "operation":"RETRACT","value":{"level":"quiet"}}]
    actual=[{"turn":1,"subject_id":"次卧","predicate":"noise_preference",
             "operation":"RETRACT","value":{"reason":"explicit_withdrawal"}}]
    assert MODULE._counts(expected,actual,event=True)==(1,0,0)


def test_abstention_is_scored_separately():
    gold,sha,payload=_perfect_payload()
    pred=next(x for x in payload["cases"] if x["id"]=="zh12")
    pred["answer"]="主卧24度"
    report=MODULE.score(gold,MODULE.validate_predictions(payload,gold,sha),
                        payload["configuration"],sha)
    assert report["answer"]["abstention_accuracy"]<1.0


def test_configuration_booleans_required_and_answers_not_llm_judged():
    gold,sha,payload=_perfect_payload()
    payload["configuration"]["context_enabled"]="yes"
    with pytest.raises(ValueError,match="booleans"):
        MODULE.validate_predictions(payload,gold,sha)
    assert MODULE._normalize_answer("26℃。") == MODULE._normalize_answer(" 26度 ")
