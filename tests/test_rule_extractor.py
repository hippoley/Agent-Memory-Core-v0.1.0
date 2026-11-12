"""Deterministic rule extractor: zero-model text extraction contract."""
import json
import pytest
from agent_memory_core.rule_extractor import RuleFactExtractor, RuleExtractorConfigError


def by_predicate(facts):
    return {f["predicate"]: f for f in facts}


def test_room_noise_preference_is_extracted_without_any_model():
    facts = RuleFactExtractor()("今晚卧室请保持安静，我要早点睡。")
    item = by_predicate(facts)["noise_preference"]
    assert item["subject_id"] == "卧室"
    assert item["value"] == {"level": "quiet"}
    assert 0.5 <= item["confidence"] <= 0.7


def test_subject_falls_back_to_configured_default():
    facts = RuleFactExtractor(default_subject="me")("我讨厌香菜")
    item = by_predicate(facts)["dislikes"]
    assert item["subject_id"] == "me"
    assert item["value"] == {"item": "香菜"}


def test_light_dim_takes_precedence_over_bright_keywords():
    facts = RuleFactExtractor()("客厅灯光太亮了，能调暗一点吗")
    item = by_predicate(facts)["light_preference"]
    assert item["value"] == {"level": "dim"}


def test_temperature_is_cast_to_integer():
    facts = RuleFactExtractor()("卧室空调调到26度")
    item = by_predicate(facts)["temperature_preference"]
    assert item["subject_id"] == "卧室"
    assert item["value"] == {"celsius": 26}


def test_allergy_and_likes_are_separate_predicates():
    facts = by_predicate(RuleFactExtractor()("我对花生过敏，但我喜欢喝咖啡"))
    assert facts["allergy"]["value"] == {"item": "花生"}
    assert facts["likes"]["value"] == {"item": "喝咖啡"}


def test_no_match_returns_empty_list():
    assert RuleFactExtractor()("今天天气不错") == []


def test_empty_or_oversized_text_is_rejected():
    with pytest.raises(ValueError):
        RuleFactExtractor()("   ")
    with pytest.raises(ValueError):
        RuleFactExtractor()("x" * 100001)


def test_duplicate_predicate_keeps_highest_confidence_match():
    facts = RuleFactExtractor()("卧室安静一点。卧室请保持安静。")
    noise = [f for f in facts if f["predicate"] == "noise_preference"]
    assert len(noise) == 1


def test_custom_rule_file_extends_behavior(tmp_path):
    rules = {"rules": [{
        "name": "charging_window",
        "regex": r"(?P<subject>car)?[^。\n]{0,6}充电[^\d。\n]{0,6}(?P<hour>\d{1,2})点",
        "predicate": "charging_start",
        "value": {"hour": "{hour:int}"},
        "confidence": 0.9,
    }]}
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    extractor = RuleFactExtractor.from_file(str(path), default_subject="car-1")
    item = by_predicate(extractor("明天晚上充电22点开始"))["charging_start"]
    assert item["subject_id"] == "car-1"
    assert item["value"] == {"hour": 22}


@pytest.mark.parametrize("bad_rule", [
    {"regex": "(", "predicate": "p", "value": {"k": "{g}"}},
    {"regex": "x", "predicate": "NotSnake", "value": {"k": "{g}"}},
    {"regex": "x", "predicate": "p", "value": {}},
    {"regex": "x", "predicate": "p", "value": {"k": ["not-a-scalar"]}},
    {"regex": "x", "predicate": "p", "value": {"k": "{g}"}, "confidence": 0},
    {"regex": "x", "predicate": "p", "value": {"k": "{g}"}, "bogus": True},
])
def test_invalid_custom_rules_fail_closed(bad_rule):
    with pytest.raises(RuleExtractorConfigError):
        RuleFactExtractor(rules=[bad_rule])


def test_server_rule_provider_dispatch(monkeypatch):
    monkeypatch.setenv("MEMORY_API_KEY", "x" * 32)
    monkeypatch.setenv("MEMORY_TENANT_ID", "test-tenant")
    monkeypatch.setenv("MEMORY_EXTRACTOR_PROVIDER", "rule")
    monkeypatch.setenv("MEMORY_RULE_DEFAULT_SUBJECT", "home")
    import agent_memory_core.server as srv
    extractor = srv.memory_extractor_factory("t1")
    assert isinstance(extractor, RuleFactExtractor)
    assert extractor.default_subject == "home"
    assert srv.extractor_provider() == "rule"


def test_negated_like_is_not_extracted_as_like():
    """零模型路径不能把「不喜欢」翻成「喜欢」，否则台账记的是反话。"""
    from agent_memory_core.rule_extractor import RuleFactExtractor

    facts = RuleFactExtractor()("书房里我不喜欢太吵，需要专心工作")
    predicates = {(f.get("predicate"), f.get("subject_id") or f.get("subject_name")) for f in facts}
    assert ("likes", "书房") not in predicates
    assert ("dislikes", "书房") in predicates
