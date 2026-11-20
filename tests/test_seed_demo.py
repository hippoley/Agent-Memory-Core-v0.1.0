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


def test_seed_failure_propagates_instead_of_claiming_demo_ready(monkeypatch):
    import pytest

    calls = []
    def fake_request(base, path, key, method="GET", payload=None):
        calls.append(payload["source_id"])
        if payload["source_id"] == seed_demo.STORY[1][0]:
            return 503, {"detail": "transient backend failure"}
        return 200, {"relations": [{"relation": "created", "subject_id": "test", "predicate": "p"}]}

    monkeypatch.setattr(seed_demo, "request", fake_request)
    with pytest.raises(RuntimeError, match=r"Demo seed incomplete \\(1/7 requests failed\\)"):
        seed_demo.seed("http://127.0.0.1:8765", "fixture-secret")
    assert calls == [source for source, _ in seed_demo.STORY]


def test_seed_success_allows_empty_relation_results(monkeypatch):
    # A successful HTTP response with no extracted fact is not a request failure;
    # the seeder reports "no facts" rather than inventing a claim.
    monkeypatch.setattr(
        seed_demo, "request", lambda *args, **kwargs: (200, {"relations": []})
    )
    seed_demo.seed("http://127.0.0.1:8765", "fixture-secret")
