"""Guardrails for the repository's existing Chinese demo-story seeder."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "seed_demo", ROOT / "scripts" / "seed_demo.py"
)
assert SPEC and SPEC.loader
seed_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed_demo)


def test_demo_story_has_stable_source_ids_and_multiple_turns():
    assert len(seed_demo.STORY) >= 5
    assert all(source.startswith("demo:script:") for source, _ in seed_demo.STORY)


def test_seed_story_contains_correction_and_multiple_domains():
    utterances = " ".join(text for _, text in seed_demo.STORY)
    assert "23" in utterances
    assert "灯" in utterances
    assert "安静" in utterances


def test_cleanup_is_opt_in_and_seed_is_a_separate_operation():
    source = (ROOT / "scripts" / "seed_demo.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--clean"' in source
    assert 'parser.add_argument("--seed"' in source
    assert "if args.clean:" in source
    assert "if args.seed:" in source
