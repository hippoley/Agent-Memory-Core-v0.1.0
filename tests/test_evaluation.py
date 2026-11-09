from agent_memory_core.evaluation import score_case


def test_extraction_score_counts_polarity_errors_as_false_positives_and_negatives():
    expected=[{"subject_name":"卧室","predicate":"noise_preference",
               "value":{"level":"quiet"},"polarity":"negative"}]
    predicted=[{"subject_name":"卧室","predicate":"noise_preference",
                "value":{"level":"quiet"},"polarity":"positive"}]
    score=score_case(expected,predicted)
    assert score["true_positive"]==0
    assert score["false_positive"]==1
    assert score["false_negative"]==1
    assert score["polarity_accuracy"]==0.0
