"""Tests for the loopback-only local structured memory extractor."""
import io
import json
import pytest

from agent_memory_core.local_extractor import LocalOpenAICompatibleExtractor


class FakeOpener:
    def __init__(self, payload):
        self.payload=payload
        self.request=None
    def open(self, request, timeout):
        self.request=request
        return io.BytesIO(json.dumps({
            "choices":[{"message":{"content":json.dumps(self.payload,ensure_ascii=False)}}]
        }).encode("utf-8"))


def test_local_extractor_preserves_chinese_negation_and_request_contract():
    opener=FakeOpener({"candidates":[{
        "subject_id":"bedroom-1","predicate":"noise_preference",
        "value":{"level":"quiet"},"confidence":0.98,"polarity":"negative",
    }]})
    extractor=LocalOpenAICompatibleExtractor(
        base_url="http://127.0.0.1:1234/v1",model="qwen-local",
        api_key="local-token",opener=opener)
    candidates=extractor("我不喜欢安静的卧室")
    assert candidates[0]["polarity"]=="negative"
    assert opener.request.full_url=="http://127.0.0.1:1234/v1/chat/completions"
    assert opener.request.get_header("Authorization")=="Bearer local-token"
    body=json.loads(opener.request.data.decode("utf-8"))
    assert body["temperature"]==0
    assert body["response_format"]=={"type":"json_object"}


def test_local_extractor_rejects_non_loopback_endpoints():
    with pytest.raises(ValueError):
        LocalOpenAICompatibleExtractor(
            base_url="https://api.example.com/v1",model="remote-model")


def test_local_extractor_rejects_malformed_candidate():
    opener=FakeOpener({"candidates":[{
        "subject_id":"bedroom-1","predicate":"noise_preference",
        "value":{"level":"quiet"},"confidence":0.9,"polarity":"maybe",
    }]})
    extractor=LocalOpenAICompatibleExtractor(
        base_url="http://localhost:1234/v1",model="qwen-local",opener=opener)
    with pytest.raises(ValueError):
        extractor("不喜欢安静")


def test_local_extractor_uses_context_only_for_grounded_references():
    opener=FakeOpener({"candidates":[{
        "subject_name":"卧室窗户","predicate":"window_preference",
        "value":{"open":"avoid"},"confidence":0.91,"polarity":"negative",
    }]})
    extractor=LocalOpenAICompatibleExtractor(
        base_url="http://localhost:1234/v1",model="qwen-local",opener=opener)
    result=extractor.extract_with_context(
        "它不要打开",
        [{"role":"user","content":"卧室窗户晚上保持关闭。"}],
    )
    assert result[0]["subject_name"]=="卧室窗户"
    body=json.loads(opener.request.data.decode("utf-8"))
    assert body["messages"][1]["content"]=="卧室窗户晚上保持关闭。"
    assert body["messages"][2]["content"]=="它不要打开"


def test_local_extractor_rejects_invalid_context_turn():
    opener=FakeOpener({"candidates":[]})
    extractor=LocalOpenAICompatibleExtractor(
        base_url="http://localhost:1234/v1",model="qwen-local",opener=opener)
    with pytest.raises(ValueError):
        extractor.extract_with_context("它不要打开",[{"role":"system","content":"bad"}])
