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
