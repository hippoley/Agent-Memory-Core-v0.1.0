"""Manual live-provider acceptance must never be mistaken for PR stub CI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/qwen-real-acceptance.yml"
DOC = ROOT / "docs/QWEN-REAL-MODEL-ACCEPTANCE.md"


def test_qwen_workflow_is_opt_in_and_requires_secret():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "  push:" not in text
    assert "  pull_request:" not in text
    assert "${{ secrets.DASHSCOPE_API_KEY }}" in text
    assert "if [[ -z" in text
    assert "MEMORY_EXTRACTOR_REQUIRE_MODEL_ATTESTATION: \"1\"" in text
    assert "--provider openai-compatible" in text
    assert "--language zh" in text


def test_qwen_workflow_is_isolated_and_retains_failure_artifacts():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "docker compose up -d --build --wait" in text
    assert "docker compose down -v" in text
    assert "127.0.0.1:8767" in text
    assert "upload-artifact@v4" in text
    assert "if: always()" in text
    assert "report_sha256" in text
    assert DOC.exists()
