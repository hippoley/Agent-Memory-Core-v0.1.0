"""Pure checks of the resource report's units and percentile calculations."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark_resources.py"
spec = importlib.util.spec_from_file_location("benchmark_resources", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)

def test_docker_memory_units_are_explicit():
    assert benchmark.parse_bytes("1GiB") == 1024 ** 3
    assert benchmark.parse_bytes("256MiB") == 256 * 1024 ** 2
    assert benchmark.parse_bytes("1.5GB") == 1500000000

def test_reject_unknown_docker_units():
    import pytest
    with pytest.raises(ValueError):
        benchmark.parse_bytes("3 nonsense")

def test_percentile_nearest_rank_and_empty():
    assert benchmark.percentile([1, 2, 3, 4, 5], .95) == 5
    assert benchmark.percentile([5, 1, 3, 4, 2], .5) == 3
    assert benchmark.percentile([], .95) is None

def test_benchmark_does_not_write_or_send_credentials_to_public_gateway():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'http://127.0.0.1:8765/v1/memories?limit=10' in text
    assert "urlopen(req" in text
    assert "POST /v1/memories" not in text
