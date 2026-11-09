"""CLI contract tests for the projection worker process."""
import pytest
from agent_memory_core.projection_worker import build_parser, _load_embedder


def test_projection_worker_parser_requires_destination():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_projection_worker_rejects_vector_without_model_configuration():
    args=build_parser().parse_args([
        "--projection","vector","--dsn","postgresql://localhost/test"])
    assert args.embedder is None
    assert args.model_id is None


def test_embedder_spec_requires_module_callable():
    with pytest.raises(ValueError):
        _load_embedder("not-a-module-callable")
