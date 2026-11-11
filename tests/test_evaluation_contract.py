from agent_memory_core.evaluation import score_case


def test_extractor_evaluation_counts_polarity_mismatch_as_fp_and_fn():
    expected=[{"subject_id":"bedroom","predicate":"noise",
               "value":{"level":"quiet"},"polarity":"negative"}]
    predicted=[{"subject_id":"bedroom","predicate":"noise",
                "value":{"level":"quiet"},"polarity":"positive"}]
    result=score_case(expected,predicted)
    assert result["true_positive"]==0
    assert result["false_positive"]==1
    assert result["false_negative"]==1
    assert result["f1"]==0.0
    assert result["polarity_accuracy"]==0.0
