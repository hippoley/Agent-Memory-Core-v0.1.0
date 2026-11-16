"""The 200-case replay tests the *actual rule extractor*, without any model claims."""
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("rule_200",ROOT/"scripts/eval/diagnose_zh_200_rules.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_subject_matching_does_not_confuse_room_names():
    assert module._subject("儿童房空调调到25度")=="儿童房"
    assert module._subject("卫生间光线太亮")=="卫生间"
    assert module._subject("主卧灯光暗，客厅开灯")=="主卧"


def test_negative_examples_not_miscounted_as_perfect_positives():
    desired=[{"subject_id":"主卧","predicate":"temperature_preference","value":{"celsius":26}}]
    wrong=[{"subject_id":"主卧","predicate":"light_preference","value":{"level":"dim"}}]
    assert module.tally(desired,wrong)==(0,1,1)


def test_offline_replay_is_labeled_extraction_only_and_200_cases():
    import json
    from agent_memory_core.rule_extractor import RuleFactExtractor
    rows=[json.loads(line) for line in module.DEFAULT_GOLD.read_text(encoding="utf-8").splitlines()]
    report=module.evaluate(rows,RuleFactExtractor.from_preset("home-zh"))
    assert report["n"]==200
    assert report["scope"]=="RULE_EXTRACTION_ONLY"
    assert report["retrieval"]=="NOT_RUN"
    assert report["answer"]=="NOT_RUN"
    assert report["oov_exact_coverage"]["total"]==30
    assert sum(x["cases"] for x in report["by_kind"].values())==200
