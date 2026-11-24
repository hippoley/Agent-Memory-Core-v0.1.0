import importlib.util
from pathlib import Path
from types import ModuleType
import sys
import pytest

spec = importlib.util.spec_from_file_location('probe_local_qwen', Path(__file__).with_name('probe_local_qwen.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Response:
    def __init__(self, body): self.body = body
    def raise_for_status(self): pass
    def json(self): return self.body

class Client:
    def get(self, url): return Response({'data': [{'id': 'qwen-local'}]})

@pytest.mark.parametrize('bad', ['https://external.example/v1/chat/completions',
    'http://127.0.0.1:8000/v1/chat/completions?key=secret',
    'http://127.0.0.1:8000/v1/other'])
def test_reject_remote_or_malformed_model_url(bad):
    with pytest.raises(ValueError): m.verify_local_url(bad)

def test_pinned_model_is_really_listed():
    assert m.verify_local_model(Client(), 'http://127.0.0.1:8000/v1/chat/completions', 'qwen-local')['model_id'] == 'qwen-local'
    with pytest.raises(ValueError): m.verify_local_model(Client(), 'http://127.0.0.1:8000/v1/chat/completions', 'wrong')

def test_preflight_uses_actual_production_decision_function(monkeypatch):
    pkg = ModuleType('agent_memory_core')
    agent = ModuleType('agent_memory_core.handoff_model_agent')
    seen = []
    def choose_device_action(**kw):
        seen.append(kw)
        return {'value': 26, 'observed_model': 'qwen-local'}
    agent.choose_device_action = choose_device_action
    monkeypatch.setitem(sys.modules, 'agent_memory_core', pkg)
    monkeypatch.setitem(sys.modules, 'agent_memory_core.handoff_model_agent', agent)
    result = m.probe(client=Client(), endpoint='http://127.0.0.1:8000/v1/chat/completions',
            model='qwen-local', device_id='bed', property_name='temperature', expected_value=26)
    assert result['effect_performed'] is False
    assert seen[0]['accepted_payload']['claims'][0]['id'].startswith('preflight-')


def test_preflight_refuses_wrong_model_answer(monkeypatch):
    pkg = ModuleType('agent_memory_core')
    agent = ModuleType('agent_memory_core.handoff_model_agent')
    agent.choose_device_action = lambda **kw: {'value': 26, 'observed_model': 'switch'}
    monkeypatch.setitem(sys.modules, 'agent_memory_core', pkg)
    monkeypatch.setitem(sys.modules, 'agent_memory_core.handoff_model_agent', agent)
    with pytest.raises(ValueError, match='contract'):
        m.probe(client=Client(), endpoint='http://127.0.0.1:8000/v1/chat/completions',
            model='qwen-local', device_id='bed', property_name='temperature', expected_value=26)


def test_challenges_have_distinct_targets_and_primary():
    targets = m.challenge_targets(26, count=3, randbelow=lambda n: 0)
    assert targets[0] == 26
    assert len(targets) == len(set(targets)) == 3
    assert all(abs(value - 26) <= 0.301 for value in targets)


def test_challenge_edge_and_invalid_count():
    assert len(m.challenge_targets(1000, count=3, randbelow=lambda n: 0)) == 3
    with pytest.raises(ValueError):
        m.challenge_targets(26, count=1)
    with pytest.raises(ValueError):
        m.challenge_targets(float('nan'))


def test_evidence_client_records_response_digest_without_body():
    class RealisticResponse:
        status_code = 200
        content = b'opaque-secret-model-output'
    class RealisticClient:
        def post(self, url, json):
            return RealisticResponse()
    client = m.EvidenceClient(RealisticClient())
    response = client.post('http://127.0.0.1:8000/v1/chat/completions', json={'model':'qwen'})
    assert response.status_code == 200
    assert client.events[0]['response_sha256'] == m.hashlib.sha256(response.content).hexdigest()
    assert 'opaque-secret' not in str(client.events)
