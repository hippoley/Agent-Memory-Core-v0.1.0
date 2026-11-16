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
