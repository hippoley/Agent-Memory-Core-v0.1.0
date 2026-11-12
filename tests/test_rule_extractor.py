"""Deterministic rule extractor: zero-model text extraction contract."""
import json
import pytest
from agent_memory_core.rule_extractor import RuleFactExtractor, RuleExtractorConfigError


def by_predicate(facts):
    return {f["predicate"]: f for f in facts}


def test_room_noise_preference_is_extracted_without_any_model():
    facts = RuleFactExtractor.from_preset("home-zh")("今晚卧室请保持安静，我要早点睡。")
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
    facts = RuleFactExtractor.from_preset("home-zh")("客厅灯光太亮了，能调暗一点吗")
    item = by_predicate(facts)["light_preference"]
    assert item["value"] == {"level": "dim"}


def test_temperature_is_cast_to_integer():
    facts = RuleFactExtractor.from_preset("home-zh")("卧室空调调到26度")
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
    facts = RuleFactExtractor.from_preset("home-zh")("卧室安静一点。卧室请保持安静。")
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

    facts = RuleFactExtractor.from_preset("home-zh")("书房里我不喜欢太吵，需要专心工作")
    predicates = {(f.get("predicate"), f.get("subject_id") or f.get("subject_name")) for f in facts}
    assert ("likes", "书房") not in predicates
    assert ("dislikes", "书房") in predicates


def test_core_module_holds_no_domain_vocabulary():
    """核心模块不许预置行业词表。领域词只能是数据，不能是代码。"""
    from pathlib import Path

    from agent_memory_core import rule_extractor

    source = Path(rule_extractor.__file__).read_text(encoding="utf-8")
    for word in ("卧室", "客厅", "厨房", "书房", "空调", "noise_preference",
                 "light_preference", "temperature_preference", "demo-home"):
        assert word not in source, f"{word!r} leaked into the core module"
    assert rule_extractor.SUBJECT_WORDS == ()


def test_default_extractor_ignores_domain_specific_utterance():
    """默认配置不认行业表达：既不该瞎抽，也不该报错。"""
    assert RuleFactExtractor()("卧室请保持安静") == []
    assert RuleFactExtractor()("客厅空调调到26度") == []


def test_default_rules_still_cover_generic_preferences():
    facts = by_predicate(RuleFactExtractor(default_subject="u1")("我讨厌香菜，也喜欢喝咖啡"))
    assert facts["dislikes"]["value"] == {"item": "香菜"}
    assert facts["likes"]["value"] == {"item": "喝咖啡"}
    assert {f["subject_id"] for f in facts.values()} == {"u1"}


def test_example_packs_ship_as_data():
    from agent_memory_core.rule_extractor import available_presets

    assert "home-zh" in available_presets()


def test_unknown_preset_fails_closed():
    with pytest.raises(RuleExtractorConfigError):
        RuleFactExtractor.from_preset("no-such-domain")
    with pytest.raises(RuleExtractorConfigError):
        RuleFactExtractor.from_preset("../etc/passwd")


def test_preset_file_supplies_subject_lexicon(tmp_path):
    """规则文件里的 subjects 决定 subject 归属，不需要改代码。"""
    path = tmp_path / "crm.json"
    path.write_text(json.dumps({
        "subjects": ["客户A", "客户B"],
        "rules": [{
            "name": "channel",
            "regex": r"(?:偏好|喜欢)(?P<channel>[^，。\s]{1,8})联系",
            "predicate": "preferred_channel",
            "value": {"channel": "{channel}"},
            "confidence": 0.8,
        }],
    }), encoding="utf-8")
    extractor = RuleFactExtractor.from_file(str(path), default_subject="unknown")
    item = by_predicate(extractor("客户A偏好邮件联系"))["preferred_channel"]
    assert item["subject_id"] == "客户A"
    assert item["value"] == {"channel": "邮件"}


def test_server_accepts_preset_name_instead_of_path(monkeypatch):
    monkeypatch.setenv("MEMORY_API_KEY", "x" * 32)
    monkeypatch.setenv("MEMORY_TENANT_ID", "test-tenant")
    monkeypatch.setenv("MEMORY_EXTRACTOR_PROVIDER", "rule")
    monkeypatch.setenv("MEMORY_RULE_PATTERNS", "home-zh")
    import agent_memory_core.server as srv

    extractor = srv.memory_extractor_factory("t1")
    assert extractor.subject_words, "preset should load its subject lexicon"
    assert by_predicate(extractor("卧室请保持安静"))["noise_preference"]["subject_id"] == "卧室"
