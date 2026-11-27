"""Adversarial contract tests: no real-model scores are produced here."""
import importlib.util
from copy import deepcopy
from pathlib import Path
import pytest

FILE = Path(__file__).resolve().parents[1] / "scripts/eval/score_future_task_utility.py"
spec = importlib.util.spec_from_file_location("future_task_utility", FILE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixture():
    gold = [{"id":"t1","accepted_answers":["26度"]},
            {"id":"t2","accepted_answers":["无法确定"]}]
    cfg = {"case_isolation":"fresh_tenant_per_task",
           "max_context_tokens":256,"original_dialogue_visible":False}
    identity = {"verified":True,"response_model":"local-qwen","system_fingerprint":"f1"}
    arms = {name: {"arm":name,"dataset_sha256":"gold-sha",
                   "capture_type":"real_agent_future_task",
                   "configuration":deepcopy(cfg),"model_runtime_identity":deepcopy(identity),
                   "run_id":"run-"+name,"cases":[{"id":"t1","answer":"不知道"},
                                                  {"id":"t2","answer":"不知道"}]}
            for name in mod.ARMS}
    arms["amc"]["cases"] = [{"id":"t1","answer":"26度"},
                            {"id":"t2","answer":"无法确定"}]
    return gold, arms


def test_paired_scoring_not_release_grade_with_only_two_cases():
    gold, arms = fixture()
    report = mod.score(gold,"gold-sha",arms)
    assert report["accuracy"]["amc"] == 1
    assert report["amc_minus_summary"] == 1
    assert report["release_gate"]["eligible"] is False
    assert mod.score(gold,"gold-sha",arms,min_cases=2)["release_gate"]["eligible"]


@pytest.mark.parametrize("mutation", [
    lambda a: a["summary"].update(run_id=a["amc"]["run_id"]),
    lambda a: a["summary"].update(dataset_sha256="wrong"),
    lambda a: a["summary"].update(capture_type="oracle"),
    lambda a: a["summary"].update(model_runtime_identity={"verified":False,"response_model":"local-qwen"}),
    lambda a: a["summary"]["model_runtime_identity"].update(response_model="wrong"),
    lambda a: a["summary"]["configuration"].update(original_dialogue_visible=True),
    lambda a: a["summary"]["configuration"].update(max_context_tokens=512),
    lambda a: a["summary"].update(cases=[{"id":"t1","answer":"26度"}]),
    lambda a: a["summary"]["cases"].__setitem__(1,{"id":"t1","answer":"duplicate"}),
])
def test_reject_unfair_or_fake_run(mutation):
    gold,arms = fixture()
    mutation(arms)
    with pytest.raises(ValueError):
        mod.score(gold,"gold-sha",arms,min_cases=2)
