"""Strict continuity required-effect manifest validation."""
import pytest
from agent_memory_core.durable_tasks import required_effect_keys


def test_named_continuity_task_requires_a_bounded_unique_effect_manifest():
    assert required_effect_keys(
        "continuity.effect.v1", {"required_effect_keys": ["a", "b"]}) == ("a", "b")
    for keys in (None, [], ["a","a"], [""], [42], [True], ["x"] * 21):
        with pytest.raises(ValueError, match="required_effect_keys"):
            required_effect_keys("continuity.effect.v1", {"required_effect_keys": keys})


def test_old_task_types_do_not_change_semantics():
    assert required_effect_keys("memory.batch.organize", {}) == ()
