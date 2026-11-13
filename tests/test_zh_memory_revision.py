"""Regression tests for Chinese correction, explicit retraction, and scoping.

These unit tests exercise the actual extractor and MemoryProcessor without a
model. They do NOT assert full multi-turn Chinese language understanding.
"""
from agent_memory_core.rule_extractor import RuleFactExtractor
from agent_memory_core.processing import MemoryProcessor


class ScopedFakeStore:
    def __init__(self, *, cardinality="one"):
        self.cardinality = cardinality
        self.forget_calls = []

    def predicate_cardinality(self, *, tenant_id, predicate):
        return self.cardinality

    def forget_many(self, *, tenant_id, subject_id, predicate):
        self.forget_calls.append((tenant_id, subject_id, predicate))
        return ["prior-memory"]


def test_home_zh_explicit_withdrawal_is_not_quiet_upsert():
    ext = RuleFactExtractor.from_preset("home-zh")
    for text, subject in [
        ("次卧不用静音了，正常就好", "次卧"),
        ("玄关不用静音", "玄关"),
        ("书房正常模式就行", "书房"),
    ]:
        candidates = ext(text)
        noise = [c for c in candidates if c["predicate"] == "noise_preference"]
        assert len(noise) == 1, (text, candidates)
        assert noise[0]["operation"] == "RETRACT", (text, candidates)
        assert noise[0]["subject_id"] == subject


def test_retract_is_scoped_by_tenant_room_and_predicate():
    store = ScopedFakeStore()
    ext = RuleFactExtractor.from_preset("home-zh")
    report = MemoryProcessor(store).process(
        tenant_id="tenant-1", text="玄关不用静音",
        source_id="utterance:withdraw-1", extractor=ext)
    assert store.forget_calls == [("tenant-1", "玄关", "noise_preference")]
    assert report.replaced == ["prior-memory"]
    assert report.created == []
    assert report.relations[0]["relation"] == "RETRACTED"


def test_retract_rejects_multi_valued_predicate():
    store = ScopedFakeStore(cardinality="many")
    report = MemoryProcessor(store).process(
        tenant_id="tenant-1", text="书房正常模式就行",
        source_id="utterance:withdraw-2",
        extractor=RuleFactExtractor.from_preset("home-zh"))
    assert not store.forget_calls
    assert report.rejected == 1


def test_never_interpret_unspecified_no_more_as_global_deletion():
    store = ScopedFakeStore()
    report = MemoryProcessor(store).process(
        tenant_id="tenant-1", text="不用了",
        source_id="utterance:ambiguous",
        extractor=RuleFactExtractor.from_preset("home-zh"))
    assert report.created == []
    assert not store.forget_calls


def test_final_temperature_after_rejected_candidate_wins():
    ext = RuleFactExtractor.from_preset("home-zh")
    facts = ext("主卧空调不要开到28度，26度就够了")
    temp = next(c for c in facts if c["predicate"] == "temperature_preference")
    assert temp["subject_id"] == "主卧"
    assert temp["value"] == {"celsius": 26}


def test_dim_not_inverted_by_negative_light_expression():
    ext = RuleFactExtractor.from_preset("home-zh")
    facts = ext("卫生间灯不用那么亮")
    light = next(c for c in facts if c["predicate"] == "light_preference")
    assert light["value"] == {"level": "dim"}


def test_invalid_retract_operation_rejected_by_rule_config():
    import pytest
    from agent_memory_core.rule_extractor import RuleExtractorConfigError
    with pytest.raises(RuleExtractorConfigError):
        RuleFactExtractor(rules=[{
            "regex": "取消", "predicate": "noise_preference",
            "value": {"reason": "withdrawal"}, "operation": "DELETE_ALL",
        }], include_defaults=False)


def test_no_retraction_candidate_without_subject_and_predicate():
    store = ScopedFakeStore()
    report = MemoryProcessor(store).process(
        tenant_id="tenant-1", text="撤回",
        source_id="utterance:bad", extractor=lambda _: [{
            "operation": "RETRACT", "subject_id": "", "predicate": "noise_preference",
            "value": {}, "confidence": 0.99,
        }])
    assert report.rejected == 1
    assert not store.forget_calls
