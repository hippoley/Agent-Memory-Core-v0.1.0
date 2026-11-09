from importlib import resources
from pathlib import Path


def test_packaged_schema_equals_repository_schema():
    packaged = resources.files("agent_memory_core").joinpath("resources/001_core.sql").read_text()
    canonical = (Path(__file__).resolve().parents[1] / "sql/postgresql/001_core.sql").read_text()
    assert packaged == canonical
