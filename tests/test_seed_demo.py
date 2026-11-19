"""Unit checks for the deterministic demo memory seed contract."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "seed_demo", ROOT / "scripts" / "seed_demo.py"
)
assert SPEC and SPEC.loader
seed_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed_demo)


def test_active_claim_excludes_superseded_and_forgotten_versions():
    rows = [
        {"id": "old", "predicate": "p", "superseded_at": "2026-01-01T00:00:00Z"},
        {"id": "forgotten", "predicate": "p", "forgotten_at": "2026-01-02T00:00:00Z"},
        {"id": "current", "predicate": "p"},
    ]
    assert seed_demo.active_claim(rows, "p")["id"] == "current"


def test_active_claim_returns_none_when_no_current_version():
    assert seed_demo.active_claim(
        [{"id": "old", "predicate": "p", "superseded_at": "2026-01-01T00:00:00Z"}],
        "p",
    ) is None


def test_demo_subjects_are_namespaced():
    source = (ROOT / "scripts" / "seed_demo.py").read_text(encoding="utf-8")
    assert '"amc-demo-user"' in source
    assert '"amc-demo-home"' in source
    assert '"amc-demo-window-01"' in source
