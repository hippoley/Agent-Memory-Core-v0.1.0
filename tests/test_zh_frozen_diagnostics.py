"""Real rule-extractor diagnostic and paired-report invariants."""
import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(path, name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_rule_diagnostic_extraction_only_and_actual_cases():
    mod=load("scripts/eval/diagnose_zh_frozen_rules.py","diag_rules")
    plain=mod.run(use_context=False)
    context=mod.run(use_context=True)
    assert plain["n"]==12 and context["n"]==12
    assert plain["scope"]=="extraction_only"
    assert context["scope"]=="extraction_only"
    assert "NOT_RUN" in plain["other_stages"]
    assert plain["dataset_sha256"]==context["dataset_sha256"]
    assert len(plain["details"])==12
    assert len(context["details"])==12
    assert context["configuration"]["context_enabled"] is True


def test_compare_rejects_different_hashes_and_names():
    comp=load("scripts/eval/compare_zh_frozen.py","compare_zh")
    a={"dataset_sha256":"a","cases":12,"configuration":{"name":"A"}}
    b={"dataset_sha256":"b","cases":12,"configuration":{"name":"B"}}
    with pytest.raises(ValueError,match="different frozen"):
        comp.compare(a,b)
    b["dataset_sha256"]="a"
    b["configuration"]["name"]="A"
    with pytest.raises(ValueError,match="distinct"):
        comp.compare(a,b)


def test_compare_tracks_real_metric_deltas():
    comp=load("scripts/eval/compare_zh_frozen.py","compare_zh_scores")
    def report(name,score):
        return {
          "dataset_sha256":"hash","cases":12,"configuration":{"name":name},
          "extraction":{"f1":score},
          "revision":{"exact_final_state_accuracy":score},
          "retrieval":{"recall_at_5":score,"mrr_at_5":score},
          "answer":{"exact_accepted_accuracy":score,"abstention_accuracy":score}
        }
    out=comp.compare(report("A",0.5),report("B",0.75))
    assert out["metrics"]["extraction_f1"]["delta"]==0.25
    assert out["n"]==12
    assert "statistical" in out["warning"]
