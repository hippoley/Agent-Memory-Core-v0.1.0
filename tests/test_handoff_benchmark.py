"""Adversarial scorer contract tests; entirely deterministic, no paid API."""
import copy
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "benchmarks" / "handoff" / "score.py"
spec = importlib.util.spec_from_file_location("handoff_score", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

CASE = {
    "case_id": "supplier-01",
    "required_facts": {"payment_period": "quarterly", "requires_approval": True},
    "expected_effects": ["supplier-approval-request"],
    "forbidden_effects": ["supplier-payment"],
}
TRACE = {
    "case_id": "supplier-01", "arm": "amc", "observer": "independent",
    "observation_complete": True, "unresolved_unknown": False,
    "observed_facts": {"payment_period": "quarterly", "requires_approval": True},
    "observed_effects": [{"effect_key": "supplier-approval-request"}],
    "elapsed_ms": 1200, "context_tokens": 500,
}


def test_good_observation_succeeds():
    report = module.score_case(CASE, TRACE)
    assert report["success"]
    assert report["fact_accuracy"] == 1


@pytest.mark.parametrize("mutation", [
    {"observed_facts": {"payment_period": "monthly", "requires_approval": True}},
    {"observed_effects": [{"effect_key": "supplier-approval-request"}] * 2},
    {"observed_effects": [{"effect_key": "supplier-payment"},
                          {"effect_key": "supplier-approval-request"}]},
    {"observed_effects": []},
    {"unresolved_unknown": True},
    {"observation_complete": False},
])
def test_false_positive_paths_fail_closed(mutation):
    trace = {**TRACE, **mutation}
    assert not module.score_case(CASE, trace)["success"]


def test_self_reporting_is_not_evidence():
    with pytest.raises(ValueError, match="observer"):
        module.score_case(CASE, {**TRACE, "observer": "agent"})


def test_all_three_arms_required_and_duplicates_rejected():
    traces = [{**TRACE, "arm": arm} for arm in module.ARMS]
    report = module.evaluate([CASE], traces)
    assert all(v["task_success_rate"] == 1 for v in report["by_arm"].values())
    with pytest.raises(ValueError, match="incomplete"):
        module.evaluate([CASE], traces[:2])
    with pytest.raises(ValueError, match="duplicate"):
        module.evaluate([CASE], traces + [copy.deepcopy(traces[0])])


def test_missing_observations_cannot_be_scored():
    with pytest.raises(ValueError, match="missing observed"):
        module.score_case(CASE, {**TRACE, "observed_effects": None})
    with pytest.raises(ValueError, match="missing measured"):
        module.score_case(CASE, {**TRACE, "elapsed_ms": None})
