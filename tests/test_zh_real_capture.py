"""Safety and provenance tests for the real four-stage capture harness."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("zh_capture",ROOT/"scripts/eval/capture_zh_frozen_http.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("url",["https://127.0.0.1:8767","http://example.com/api",
                                   "http://user:secret@localhost:8767","file:///tmp/a"])
def test_capture_rejects_unsafe_endpoints(url):
    with pytest.raises(ValueError):
        module.loopback_url(url)


def test_capture_extraction_records_raw_candidates_even_if_store_rejects():
    class MockExtractor:
        def __call__(self,text):
            return [{"subject_id":"主卧","predicate":"temperature_preference",
                     "value":{"celsius":26},"confidence":0.2}]
    got=module.extract(MockExtractor(),"主卧温度26度",None,context=False)
    event=module._candidate_event(got[0],0)
    assert event=={"turn":0,"subject_id":"主卧","predicate":"temperature_preference",
                   "value":{"celsius":26},"operation":"UPSERT"}


def test_capture_no_silent_model_context_fallback():
    with pytest.raises(ValueError,match="does not support"):
        module.extract(lambda text: [],"书房正常模式就行","书房静音模式",context=True)


def test_capture_never_mutates_nonempty_tenant():
    class Memory:
        def current(self):
            return [{"subject_id":"主卧","predicate":"test","value":{"x":1}}]
        def reset_active(self):
            raise AssertionError("Should not delete existing data")
    with pytest.raises(RuntimeError,match="not empty"):
        module.collect([{"id":"x","turns":[],"query":"q"}],Memory(),None,None,
                       context=False,prefix="x")


def test_capture_real_stages_from_separate_adapters():
    class Memory:
        def __init__(self):self.rows=[];self.writes=[]
        def current(self):return list(self.rows)
        def history(self):return []
        def reset_active(self):self.rows=[]
        def call(self,method,path,**kw):
            if path=="/v1/memories/process":
                c=kw["json"]["candidates"][0]
                self.rows=[{**c,"id":"id-1"}]
                self.writes.append(c)
                return {"created":["id-1"]}
            if path=="/v1/search":
                return {"items":[{"subject_id":"书房","predicate":"temperature_preference",
                                   "value":{"celsius":25}}]}
            raise AssertionError(path)
    class Answer:
        def answer(self,question,hits):
            assert hits and hits[0]["value"]=={"celsius":25}
            return "25度"
    extractor=lambda text:[{"subject_id":"书房","predicate":"temperature_preference",
                             "value":{"celsius":25}}]
    memory=Memory()
    gold=[{"id":"sample","turns":["书房温度25度"],"query":"书房温度多少"}]
    records=module.collect(gold,memory,Answer(),extractor,context=False,prefix="fixture")
    assert records[0]["extracted"][0]["value"]=={"celsius":25}
    assert records[0]["current"][0]["predicate"]=="temperature_preference"
    assert records[0]["retrieved"][0]["subject_id"]=="书房"
    assert records[0]["answer"]=="25度"
    assert memory.current()==[]

def test_empty_active_but_existing_history_is_rejected_without_mutation():
    class Memory:
        def current(self): return []
        def history(self): return [{"id":"forgotten-previous-user-claim"}]
        def reset_active(self): raise AssertionError("Must not modify tenant")
    with pytest.raises(RuntimeError,match="historical claims"):
        module.collect([{"id":"sample","turns":[],"query":"x"}],Memory(),None,None,
                       context=False,prefix="test")


def test_interrupted_model_capture_cleans_its_own_active_claims():
    class Memory:
        def __init__(self): self.rows=[];self.cleanups=0
        def current(self): return list(self.rows)
        def history(self): return []
        def reset_active(self):
            self.rows=[]
            self.cleanups+=1
        def call(self,method,path,**kwargs):
            if path=="/v1/memories/process":
                self.rows=[{"subject_id":"书房","predicate":"noise_preference",
                            "value":{"level":"quiet"}}]
                return {"created":["some-id"]}
            raise AssertionError(path)
    class Extractor:
        def __init__(self):self.n=0
        def __call__(self,text):
            self.n+=1
            if self.n==2:
                raise RuntimeError("simulated local model outage")
            return [{"subject_id":"书房","predicate":"noise_preference",
                     "value":{"level":"quiet"}}]
    memory=Memory()
    with pytest.raises(RuntimeError,match="simulated local model outage"):
        module.collect([{"id":"sample","turns":["书房保持安静","书房别太吵"],"query":"x"}],
                       memory,None,Extractor(),context=False,prefix="test")
    assert memory.current()==[]
    assert memory.cleanups>=2


def test_capture_provides_chronological_bounded_multiturn_context():
    turns = ["主卧晚上开窗", "冬天不要开", "儿童房绝对不要", "那个房间指主卧", "现在主卧呢"]
    class ContextExtractor:
        def __init__(self): self.seen = []
        def __call__(self, text):
            self.seen.append((text, []))
            return [{"subject_id":"主卧","predicate":"window_preference",
                     "value":{"mode":"conditional"}}]
        def extract_with_context(self, text, previous):
            self.seen.append((text, list(previous)))
            return [{"subject_id":"主卧","predicate":"window_preference",
                     "value":{"mode":"conditional"}}]
    class Memory:
        def current(self): return []
        def history(self): return []
        def reset_active(self): pass
        def call(self,method,path,**kw):
            if path=="/v1/memories/process":
                assert len(kw["json"]["candidates"])==1
                return {"created":[]}
            if path=="/v1/search": return {"items":[]}
            raise AssertionError(path)
    class Answer:
        def answer(self,question,hits): return "无法确定"
    extractor=ContextExtractor()
    records=module.collect(
        [{"id":"context-4","turns":turns,"query":"主卧什么时候开窗"}],
        Memory(),Answer(),extractor,context=True,prefix="scope",context_window=3)
    assert len(records)==1
    assert extractor.seen==[
        (turns[0],[]),
        (turns[1],turns[:1]),
        (turns[2],turns[:2]),
        (turns[3],turns[:3]),
        (turns[4],turns[1:4]),
    ]


def test_capture_rejects_invalid_context_window_before_memory_write():
    class NeverTouch:
        def current(self): raise AssertionError("Should not touch DB")
    for invalid in (0,4,17,True,-1):
        with pytest.raises(ValueError,match="context_window"):
            module.collect([],NeverTouch(),None,None,
                           context=True,prefix="x",context_window=invalid)


def test_context_disabled_cannot_see_prior_utterances():
    class Extractor:
        def __init__(self): self.calls=[]
        def __call__(self,text):
            self.calls.append(text)
            return []
        def extract_with_context(self,text,context_turns):
            raise AssertionError("No context should be supplied")
    ext=Extractor()
    assert module.extract(ext,"第二句",["第一句"],context=False)==[]
    assert ext.calls==["第二句"]


@pytest.mark.parametrize("prior",[
    ["正常上下文"]*4,
    ["有效", ""],
    ["有效", " "*10],
    ["有效", "a"*2001],
    ["有效", 123],
])
def test_capture_rejects_invalid_history_before_model_invocation(prior):
    class NeverCall:
        def __call__(self, text): raise AssertionError("Extractor must not run")
        def extract_with_context(self, text, turns):
            raise AssertionError("Extractor must not run")
    with pytest.raises(ValueError,match="context_turns"):
        module.extract(NeverCall(),"当前对话",prior,context=True)


def test_capture_retract_scores_actual_expected_old_value_not_reason():
    candidate = {
        "subject_id":"次卧", "predicate":"noise_preference",
        "operation":"RETRACT", "value":{"reason":"explicit-withdrawal"},
        "expected_value":{"level":"quiet"},
    }
    event = module._candidate_event(candidate,1)
    assert event == {"turn":1,"subject_id":"次卧","predicate":"noise_preference",
                     "operation":"RETRACT","value":{"level":"quiet"}}
    # Preserve the original model candidate; the API still receives its reason
    # and its compare-and-withdraw precondition, not an overwritten candidate.
    assert candidate["value"] == {"reason":"explicit-withdrawal"}
    assert candidate["expected_value"] == {"level":"quiet"}


def test_capture_ungrounded_retract_never_invents_old_value():
    candidate = {
        "subject_id":"次卧", "predicate":"noise_preference",
        "operation":"RETRACT", "value":{"reason":"unknown-old-value"},
    }
    event = module._candidate_event(candidate,1)
    assert event["value"] == {"reason":"unknown-old-value"}
    assert event["value"] != {"level":"quiet"}


def test_runtime_identity_rejects_model_switch_and_unmatched_response():
    class Extractor:
        def __init__(self, calls): self.calls=calls
        def attestation_snapshot(self):
            return {"calls":self.calls,"cryptographic_weight_identity":False}
    base={"response_model":"qwen-local","identity_status":"matched",
          "model_digest":"model-sha","runtime_version":"0.12"}
    identity=module.extractor_runtime_identity(Extractor([base,dict(base)]))
    assert identity["verified"] is True
    assert identity["model_digest"]=="model-sha"
    assert identity["calls"]==2
    assert identity["cryptographic_weight_identity"] is False
    for bad in (
        [base,{**base,"response_model":"other"}],
        [base,{**base,"model_digest":"changed"}],
        [base,{**base,"identity_status":"mismatch"}],
    ):
        with pytest.raises(RuntimeError,match="identity"):
            module.extractor_runtime_identity(Extractor(bad))


def test_runtime_identity_distinguishes_unattested_adapter_from_real_identity():
    assert module.extractor_runtime_identity(lambda text: []) == {
        "verified":False,"reason":"adapter_has_no_runtime_attestation"}
    class NoCalls:
        def attestation_snapshot(self): return {"calls":[]}
    with pytest.raises(RuntimeError,match="no attested"):
        module.extractor_runtime_identity(NoCalls())


def test_answer_model_runtime_refuses_mismatched_or_changed_fingerprint():
    class Response:
        def __init__(self,model,fingerprint=None):
            self.model,self.fingerprint=model,fingerprint
        def raise_for_status(self): pass
        def json(self):
            return {"model":self.model, "system_fingerprint":self.fingerprint,
                    "choices":[{"message":{"content":"无法确定"}}]}
    class Client:
        def __init__(self,items):self.items=iter(items)
        def post(self,*args,**kwargs):return next(self.items)
        def close(self):pass
    answer=module.LocalAnswer("http://127.0.0.1:1234/v1/chat/completions","answer-qwen")
    answer.client.close()
    answer.client=Client([Response("answer-qwen","same"),Response("answer-qwen","same")])
    assert answer.answer("问题",[])=="无法确定"
    assert answer.answer("问题",[])=="无法确定"
    assert answer.identity_snapshot()["calls"]==2
    assert answer.identity_snapshot()["system_fingerprint"]=="same"
    answer.client=Client([Response("other-model","same")])
    with pytest.raises(RuntimeError,match="identity mismatch"):
        answer.answer("问题",[])
    answer.client=Client([Response("answer-qwen","changed")])
    with pytest.raises(RuntimeError,match="fingerprint changed"):
        answer.answer("问题",[])
    assert answer.identity_snapshot()["calls"]==2


def test_answer_model_runtime_rejects_missing_response_model():
    class Response:
        def raise_for_status(self):pass
        def json(self):
            return {"choices":[{"message":{"content":"25度"}}]}
    class Client:
        def post(self,*args,**kwargs):return Response()
        def close(self):pass
    answer=module.LocalAnswer("http://127.0.0.1:1234/v1/chat/completions","answer-qwen")
    answer.client.close()
    answer.client=Client()
    with pytest.raises(RuntimeError,match="identity mismatch"):
        answer.answer("温度多少",[])
    with pytest.raises(RuntimeError,match="never invoked"):
        answer.identity_snapshot()


def test_strict_case_keys_require_distinct_keys_per_story():
    module.validate_case_keys(["key-one", "key-two"], 2, "key-one")
    for keys,n,first in (
        (["key-one"],2,"key-one"),
        (["key-one","key-one"],2,"key-one"),
        (["key-one",""],2,"key-one"),
        (["key-one","key-two"],2,"wrong-first"),
    ):
        with pytest.raises(ValueError):
            module.validate_case_keys(keys,n,first)


def test_strict_case_isolation_checks_actual_tenants_not_just_distinct_keys():
    base = {"tenant_id":"tenant-a","aliases_enabled":True,
            "alias_count":1,"alias_sha256":"a"*64}
    seen=set()
    assert module.validate_isolated_tenant(base,base,seen)=="tenant-a"
    assert seen=={"tenant-a"}
    with pytest.raises(RuntimeError,match="distinct authenticated tenant"):
        module.validate_isolated_tenant(base,base,seen)
    other={**base,"tenant_id":"tenant-b"}
    assert module.validate_isolated_tenant(other,base,seen)=="tenant-b"
    invalid={**base,"tenant_id":"tenant-c","alias_sha256":"b"*64}
    with pytest.raises(RuntimeError,match="inconsistent search"):
        module.validate_isolated_tenant(invalid,base,seen)
    assert "tenant-c" not in seen


def test_extractor_identity_rejects_disappearing_optional_evidence():
    class Extractor:
        def __init__(self, calls): self.calls = calls
        def attestation_snapshot(self):
            return {"calls": self.calls, "cryptographic_weight_identity": False}

    baseline = {"response_model": "qwen-local", "identity_status": "matched",
                "model_digest": "sha256-pinned", "system_fingerprint": "host-a",
                "runtime_version": "1.0"}
    for field in ("model_digest", "system_fingerprint", "runtime_version"):
        partial = dict(baseline)
        partial.pop(field)
        with pytest.raises(RuntimeError, match="identity changed"):
            module.extractor_runtime_identity(Extractor([baseline, partial]))
        with pytest.raises(RuntimeError, match="identity changed"):
            module.extractor_runtime_identity(Extractor([partial, baseline]))


def test_answer_identity_rejects_fingerprint_presence_change():
    class Response:
        def __init__(self, fingerprint):
            self.fingerprint = fingerprint
        def raise_for_status(self): pass
        def json(self):
            result = {"model": "qwen-answer",
                      "choices": [{"message": {"content": "无法确定"}}]}
            if self.fingerprint is not None:
                result["system_fingerprint"] = self.fingerprint
            return result
    class Client:
        def __init__(self, fingerprints):
            self.items = iter(Response(fp) for fp in fingerprints)
        def post(self, *args, **kwargs): return next(self.items)
        def close(self): pass
    for pair in (("host-a", None), (None, "host-a")):
        answer = module.LocalAnswer("http://127.0.0.1:1234/v1/chat/completions",
                                    "qwen-answer")
        answer.client.close()
        answer.client = Client(pair)
        try:
            answer.answer("问题", [])
            with pytest.raises(RuntimeError, match="fingerprint disappeared or appeared"):
                answer.answer("问题", [])
            assert answer.identity_snapshot()["calls"] == 1
        finally:
            answer.close()


def test_inactive_history_evidence_distinguishes_withdrawn_from_never_known():
    former = {"subject_id": "次卧", "predicate": "noise_preference",
              "value": {"level": "quiet"}, "superseded_at": "2026-11-23T08:00:00Z"}
    assert module.inactive_history_evidence(
        "次卧的静音偏好还生效吗？", [], [former], []) == [
            {"subject": "次卧", "predicate": "noise_preference",
             "status": "previously_recorded_but_not_current"}]
    assert module.inactive_history_evidence(
        "次卧的静音偏好还生效吗？", [], [], []) == []
    assert module.inactive_history_evidence(
        "书房静音偏好还生效吗？", [], [former], []) == []
    assert module.inactive_history_evidence(
        "次卧的静音偏好还生效吗？", [], [former],
        [{"subject_id": "次卧", "predicate": "noise_preference",
          "value": {"level": "quiet"}}]) == []
    # A current replacement makes even an old superseded claim irrelevant as
    # withdrawal evidence, rather than resurrecting an obsolete preference.
    current = [{"subject_id": "次卧", "predicate": "noise_preference",
                "value": {"level": "background_music"}}]
    assert module.inactive_history_evidence(
        "次卧的静音偏好还生效吗？", current, [former], []) == []
    assert module.inactive_history_evidence(
        "次卧的静音偏好还生效吗？", [],
        [{**former, "superseded_at": None}], []) == []


def test_answer_receives_inactive_status_only_with_history_evidence():
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"model": "answer-qwen",
                    "choices": [{"message": {"content": "已取消"}}]}
    class Client:
        def __init__(self): self.payloads = []
        def post(self, url, json):
            self.payloads.append(json)
            return Response()
        def close(self): pass
    answer = module.LocalAnswer(
        "http://127.0.0.1:1234/v1/chat/completions", "answer-qwen")
    answer.client.close()
    answer.client = Client()
    try:
        evidence = [{"subject": "次卧", "predicate": "noise_preference",
                     "status": "previously_recorded_but_not_current"}]
        assert answer.answer("次卧偏好取消了吗？", [], evidence) == "已取消"
        payload = answer.client.payloads[0]["messages"][1]["content"]
        assert "previously_recorded_but_not_current" in payload
        assert '"level": "quiet"' not in payload
        answer.answer("卧室喜欢几度？", [])
        payload_without = answer.client.payloads[1]["messages"][1]["content"]
        assert "previously_recorded_but_not_current" not in payload_without
    finally:
        answer.close()


def test_collect_passes_canonical_inactive_status_to_answerer():
    class Memory:
        def current(self): return []
        def history(self):
            return [{"subject_id": "次卧", "predicate": "noise_preference",
                     "value": {"level": "quiet"}, "superseded_at": "2026-11-23T08:00:00Z"}]
        def reset_active(self): pass
        def call(self, method, path, **kw):
            if path == "/v1/memories/process": return {"created": []}
            if path == "/v1/search": return {"items": []}
            raise AssertionError(path)
    class Extractor:
        def __call__(self, text): return []
    class Answer:
        def __init__(self): self.evidence = None
        def answer(self, question, hits, inactive_evidence=None):
            self.evidence = inactive_evidence
            return "已取消"
    answer = Answer()
    cases = [{"id": "withdrawal", "turns": ["次卧不用静音了"],
              "query": "次卧静音偏好现在还在吗？"}]
    captured = module.collect(cases, Memory(), answer, Extractor(),
                              context=False, prefix="test")
    assert captured[0]["inactive_history_evidence"] == answer.evidence
    assert answer.evidence[0]["status"] == "previously_recorded_but_not_current"
