from agent_memory_core.vector_validation import vector_literal
import pytest


def test_vector_rejects_nan_inf_zero_and_wrong_dimension():
    for values in ([0.0]*384,[float("nan")]+[0.0]*383,
                   [float("inf")]+[0.0]*383,[1.0]*383):
        with pytest.raises(ValueError):
            vector_literal(values)


def test_chinese_embedding_512_profile():
    values=[1.0]+[0.0]*511
    assert vector_literal(values).startswith("[1,")
    with pytest.raises(ValueError):
        vector_literal(values, dimension=384)


def test_vector_valid_profile():
    vec=vector_literal([1.0]+[0.0]*383)
    assert vec.startswith("[1,") and vec.endswith("]")
