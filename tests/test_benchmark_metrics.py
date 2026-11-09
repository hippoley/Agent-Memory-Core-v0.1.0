from agent_memory_core.benchmarking import percentile


def test_percentile_handles_empty_and_uses_nearest_rank_index():
    assert percentile([],0.95) is None
    assert percentile([1,2,3,4,5],0.50)==3
    assert percentile([1,2,3,4,5],0.95)==5
