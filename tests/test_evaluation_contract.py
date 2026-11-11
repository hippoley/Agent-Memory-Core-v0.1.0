from scripts.evaluate_local_extractor import score


def test_extractor_evaluation_counts_polarity_mismatch_as_fp_and_fn():
    rows=[{
        "category":"negation",
        "expected_facts":[{"subject_id":"bedroom","predicate":"noise",
                           "value":{"level":"quiet"},"polarity":"negative"}],
        "predicted_facts":[{"subject_id":"bedroom","predicate":"noise",
                            "value":{"level":"quiet"},"polarity":"positive"}],
        "error":None,"latency_ms":1.0,
    }]
    result=score(rows)
    assert result["micro_f1"]==0.0
    assert result["correction_accuracy"]==0.0
    assert result["categories"]["negation"]["fp"]==1
    assert result["categories"]["negation"]["fn"]==1
