from pathlib import Path
import importlib.util

_path=Path(__file__).resolve().parents[1]/"scripts"/"evaluate_local_extractor.py"
_spec=importlib.util.spec_from_file_location("evaluate_local_extractor",_path)
_module=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
score=_module.score


def test_extractor_benchmark_reports_fact_f1_and_correction_accuracy():
    fact={"subject_id":"bedroom","predicate":"noise","value":{"level":"quiet"}}
    rows=[
        {"category":"correction","expected_facts":[fact],"predicted_facts":[fact],
         "latency_ms":12.0,"error":None},
        {"category":"no_fact","expected_facts":[],"predicted_facts":[],
         "latency_ms":18.0,"error":None},
    ]
    result=score(rows)
    assert result["micro_f1"]==1.0
    assert result["correction_accuracy"]==1.0
    assert result["correction_cases"]==1
    assert result["extractor_errors"]==0


def test_extractor_benchmark_penalizes_hallucinated_facts_and_wrong_corrections():
    expected={"subject_id":"bedroom","predicate":"noise","value":{"level":"quiet"}}
    wrong={"subject_id":"bedroom","predicate":"noise","value":{"level":"loud"}}
    result=score([{"category":"negation","expected_facts":[expected],
        "predicted_facts":[wrong],"latency_ms":10.0,"error":None}])
    assert result["micro_f1"]==0.0
    assert result["correction_accuracy"]==0.0
