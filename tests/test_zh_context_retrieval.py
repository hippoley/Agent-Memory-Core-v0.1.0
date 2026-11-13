"""Chinese coreference and domain predicate retrieval regression contracts."""
import json
import pytest

from agent_memory_core.rule_extractor import RuleFactExtractor, RuleExtractorConfigError
from agent_memory_core.hybrid import HybridMemorySearch


def test_no_context_does_not_guess_normal_mode():
    ext = RuleFactExtractor.from_preset("home-zh")
    assert ext("书房正常模式就行") == []


def test_exact_one_turn_context_proposes_retraction():
    ext = RuleFactExtractor.from_preset("home-zh")
    result = ext.extract_with_context("书房正常模式就行", ["书房静音模式"])
    assert len(result) == 1
    assert result[0]["subject_id"] == "书房"
    assert result[0]["predicate"] == "noise_preference"
    assert result[0]["operation"] == "RETRACT"


@pytest.mark.parametrize("text,prior", [
    ("正常模式就行", "书房静音模式"),  # no current explicit subject
    ("书房正常模式就行", "客厅静音模式"),  # cross-room
    ("书房灯正常模式就行", "书房静音模式"),  # unrelated target language
    ("书房正常模式就行", "书房空调调到26度"),  # wrong predicate
    ("书房正常模式就行", "书房静音模式。客厅安静些"),  # multi-fact context
])
def test_context_fail_closed_on_nonunique_or_unrelated_antecedent(text, prior):
    ext = RuleFactExtractor.from_preset("home-zh")
    assert ext.extract_with_context(text, [prior]) == []


def test_multi_turn_context_does_not_infer_distant_antecedent():
    ext = RuleFactExtractor.from_preset("home-zh")
    assert ext.extract_with_context(
        "书房正常模式就行", ["书房静音模式", "客厅开灯"]
    ) == []


def test_context_rules_are_opt_in_and_validated(tmp_path):
    doc = {"rules": [{"regex": "静音", "predicate": "noise_preference",
                      "value": {"level": "quiet"}}],
           "context_rules": [{"current_regex": "(", "anchor_predicate": "noise_preference"}]}
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(RuleExtractorConfigError):
        RuleFactExtractor.from_file(str(path))


def test_query_alias_validation_and_config():
    ext = RuleFactExtractor.from_preset("home-zh")
    assert ext("主卧温度20度")  # original extractor behavior remains available
    with pytest.raises(ValueError):
        HybridMemorySearch(None, query_aliases={"温度": "';DROP TABLE memory_claims;--"})


def test_aliases_are_bound_parameters_not_sql_fragments():
    executed = []

    class Conn:
        def execute(self, sql, params):
            executed.append((sql, list(params)))
            if "SELECT id::text, subject_id,predicate" in sql:
                return [("a","主卧","temperature_preference",{"celsius":26},"s",None,None),
                        ("b","主卧","light_preference",{"level":"dim"},"s",None,None)]
            return [("a",), ("b",)]

    class Store:
        conn = Conn()

    svc = HybridMemorySearch(Store(), query_aliases={"温度":"temperature_preference"})
    report = svc.search_detailed(tenant_id="t", query="主卧温度", limit=2)
    assert report.hits[0].memory_id == "a"
    sql, params = executed[0]
    assert "temperature_preference" not in sql
    assert "temperature_preference" in params
    assert "主卧" in params
