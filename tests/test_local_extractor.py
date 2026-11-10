"""Local extractor protocol and no-cloud security boundary."""
import io
import json
import pytest
from agent_memory_core.local_extractor import OllamaFactExtractor


class FakeHTTP:
    def __init__(self,data):
        self.data=data
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def read(self,n):
        return self.data


def test_local_ollama_produces_structured_facts_without_remote_call(monkeypatch):
    import urllib.request
    captured={}
    def stub(req,timeout):
        captured["url"]=req.full_url
        captured["payload"]=json.loads(req.data)
        return FakeHTTP(json.dumps({"response":json.dumps({"facts":[{
            "subject_id":"bedroom","predicate":"noise",
            "value":{"level":"quiet"},"confidence":0.94
        }]})}).encode())
    monkeypatch.setattr(urllib.request,"urlopen",stub)
    extractor=OllamaFactExtractor(model="local-test-model")
    result=extractor("以后卧室晚上要安静")
    assert result[0]["value"]["level"]=="quiet"
    assert captured["url"]=="http://127.0.0.1:11434/api/generate"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["options"]["temperature"]==0


@pytest.mark.parametrize("endpoint",[
    "https://api.example.com/api/generate",
    "http://169.254.169.254/api/generate",
    "http://127.0.0.1:11434/not-an-api",
    "http://user:pass@localhost/api/generate",
    "http://localhost:11434/api/generate?redirect=http://example.com",
])
def test_local_adapter_rejects_nonloopback_or_unexpected_urls(endpoint):
    with pytest.raises(ValueError):
        OllamaFactExtractor(model="m",endpoint=endpoint)


def test_local_adapter_rejects_malformed_response(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,"urlopen",lambda *a,**kw:
                         FakeHTTP(b'{"response":"{\"facts\":{\"wrong\":true}}"}'))
    with pytest.raises(RuntimeError):
        OllamaFactExtractor(model="m")("text")


def test_local_adapter_fails_closed_on_network_error(monkeypatch):
    import urllib.request
    from urllib.error import URLError
    def unavailable(*args,**kwargs):
        raise URLError("offline")
    monkeypatch.setattr(urllib.request,"urlopen",unavailable)
    with pytest.raises(RuntimeError,match="unavailable"):
        OllamaFactExtractor(model="m")("some fact")
