"""Static safety contracts only: this is NOT a model evaluation pass."""
import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_dangerous_probe_has_five_frozen_distinct_categories():
    path=ROOT/"scripts/eval/probe_zh_dangerous_live.py"
    code=path.read_text(encoding="utf-8")
    ast.parse(code)
    for case in ("negation","explicit_retract","clear_anaphora",
                 "ambiguous_anaphora","predicate_subject_collision"):
        assert 'execute("'+case+'"' in code
    assert "ground_with_memory" in code
    assert "model_call_attestation" in code
    assert 'False for c in cases' not in code
    assert "len(cases)==5" in code

def test_billable_qwen_workflow_is_manual_and_has_failure_receipt():
    code=(ROOT/".github/workflows/qwen-dangerous-zh-acceptance.yml").read_text()
    assert "workflow_dispatch:" in code
    assert "  push:" not in code
    assert "  pull_request:" not in code
    assert "secrets.DASHSCOPE_API_KEY" in code
    assert "scripts/eval/probe_zh_dangerous_live.py" in code
    assert "if: always()" in code
    assert "qwen-real-dangerous-zh-evidence" in code
    assert "docker compose down -v" in code
