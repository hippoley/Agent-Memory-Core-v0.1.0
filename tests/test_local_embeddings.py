from agent_memory_core.local_embeddings import local_sentence_transformer
import pytest


def test_local_embedding_loader_refuses_missing_path_before_import(tmp_path):
    with pytest.raises(ValueError,match="existing local directory"):
        local_sentence_transformer(str(tmp_path/"not-downloaded"))
