"""Non-destructive repository hygiene invariants."""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_archived_historical_records_and_one_launch_owner():
    assert not (ROOT / "docs/LAUNCH-KIT.md").exists()
    assert (ROOT / "docs/COMMUNITY-LAUNCH.md").is_file()
    for name in ("REFERENCE-README.md", "BRANCH-CONSOLIDATION-20261113.md",
                 "BRANCH-INTEGRATION-AUDIT.md", "MILESTONE-TRIPLE-STORE-2026-11-09.md"):
        assert (ROOT / "docs/archive" / name).is_file()
        assert not (ROOT / "docs" / name).exists()


def test_diagnostics_require_explicit_target_and_write_opt_in():
    for name in ("diag_vector.py", "diag_fts.py"):
        source = (ROOT / "scripts/eval" / name).read_text()
        ast.parse(source)
        assert "local-dev-only@127.0.0.1:55432" not in source
        assert "lme-eval-key-" not in source
    source = (ROOT / "scripts/eval/diag_vector.py").read_text()
    assert "--allow-write" in source
    assert 'if __name__ == "__main__":' in source


def test_vector_guard_rejects_remote_target():
    # Import without executing the old write-on-import behavior.
    spec = importlib.util.spec_from_file_location(
        "vector_diag", ROOT / "scripts/eval/diag_vector.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.local_url("http://127.0.0.1:8767") == "http://127.0.0.1:8767"
    with pytest.raises(ValueError):
        module.local_url("https://remote.example/api")
