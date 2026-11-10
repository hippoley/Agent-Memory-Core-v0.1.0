from pathlib import Path
import importlib.util
import pytest

_path=Path(__file__).resolve().parents[1]/"scripts"/"evaluate_retrieval.py"
_spec=importlib.util.spec_from_file_location("evaluate_retrieval",_path)
_module=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
score_query=_module.score_query


def test_retrieval_metrics_use_document_recall_and_reciprocal_rank():
    recall,rr,rank=score_query(["a","b","c"],["b","x","a","z","q","c"])
    assert recall==pytest.approx(2/3)
    assert rr==pytest.approx(1/1)
    assert rank==1


def test_retrieval_mrr_is_zero_when_no_relevant_result_in_top_ten():
    recall,rr,rank=score_query(["wanted"],["x","y","z"])
    assert recall==0
    assert rr==0
    assert rank is None


def test_retrieval_metric_rejects_empty_gold_set():
    with pytest.raises(ValueError):
        score_query([],["x"])
