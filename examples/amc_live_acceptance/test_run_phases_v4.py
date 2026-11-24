"""Stage-level tests of v4 runner with fake external boundaries (never real devices)."""
import json
import sys
from pathlib import Path
from types import ModuleType
import pytest

import run_live_handoff as runner


class Response:
    def __init__(self, body=None):
        self.body = body or {}
        self.status_code = 200
        self.content = b'{"fake":true}'
    def raise_for_status(self): pass
    def json(self): return self.body


class Http:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def post(self, url, **kw):
        if 'command' in url:
            state['attempt'] += 1
            if state['command_fail']:
                raise TimeoutError('ambiguous command timeout')
            state['value'] = kw['json']['value']
        return Response()
    def get(self, url, **kw):
        state['read'] += 1
        return Response({'device_id':'master-bedroom', 'temperature':state['value'], 'source':'hardware'})


class Memory:
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


def fake_executor(**kw):
    kw['observer']('master-bedroom','temperature')
    kw['actuator']('master-bedroom','temperature',26)
    kw['observer']('master-bedroom','temperature')
    return type('Receipt', (), {'__dataclass_fields__':{'handoff_id':None}, 'handoff_id':'fake-handoff'})()


def setup(monkeypatch, tmp_path, *, command_fail=False, preflight_fail=False):
    global state
    state = {'value':24, 'attempt':0, 'read':0, 'command_fail':command_fail}
    names = {'MEMORY_API_URL':'http://127.0.0.1:8765', 'MEMORY_API_KEY':'secret',
             'MEMORY_TENANT_ID':'test',
             'QWEN_CHAT_COMPLETIONS_URL':'http://127.0.0.1:8000/v1/chat/completions',
             'QWEN_MODEL_ID':'local-qwen',
             'DEVICE_COMMAND_URL':'https://command.local/command',
             'DEVICE_OBSERVER_URL':'https://observer.local/read',
             'DEVICE_COMMAND_TOKEN':'first-token', 'DEVICE_OBSERVER_TOKEN':'second-token'}
    for k,v in names.items(): monkeypatch.setenv(k,v)
    monkeypatch.setattr(runner.httpx, 'Client', Http)
    pkg = ModuleType('agent_memory_core')
    sdk = ModuleType('agent_memory_core.sdk')
    handoff = ModuleType('agent_memory_core.handoff_model_agent')
    probe = ModuleType('probe_local_qwen')
    sdk.MemoryClient = Memory
    handoff.execute_model_guided_handoff = fake_executor
    probe.challenge_targets = lambda target, count: [target, target+.1, target-.1]
    class EvidenceClient:
        def __init__(self, client):self.events=[]
    probe.EvidenceClient = EvidenceClient
    def fake_probe(*, client, **kwargs):
        if preflight_fail:
            raise ValueError('model tool decision wrong')
        client.events.append({'request_sha256': 'a'*64, 'response_sha256':'b'*64})
        return {'value':kwargs['expected_value']}
    probe.probe = fake_probe
    for name,module in [('agent_memory_core',pkg), ('agent_memory_core.sdk',sdk),
                        ('agent_memory_core.handoff_model_agent',handoff),
                        ('probe_local_qwen',probe)]:
        monkeypatch.setitem(sys.modules,name,module)
    output = tmp_path/'receipt.json'
    monkeypatch.setattr(sys,'argv',['run_live_handoff.py','--device','master-bedroom',
        '--property','temperature','--target','26','--safe-min','20','--safe-max','30',
        '--output',str(output),'--confirm-live-device-write'])
    return output


def test_v4_success_has_stage_evidence(monkeypatch,tmp_path):
    out=setup(monkeypatch,tmp_path)
    assert runner.main()==0
    report=json.loads(out.read_text())
    assert report['status']=='verified_by_endpoint_readback'
    assert report['actuation_attempted'] and report['actuation_acknowledged']
    assert report['readback_after_attempt_observed']
    assert state['attempt']==1 and state['read']==2
    assert len(report['model_preflight']['challenge_targets'])==3


def test_v4_ambiguous_write_is_unknown_and_not_retried(monkeypatch,tmp_path):
    out=setup(monkeypatch,tmp_path,command_fail=True)
    assert runner.main()==1
    report=json.loads(out.read_text())
    assert report['status']=='unknown_effect'
    assert report['phase']=='requires_manual_reconciliation'
    assert report['actuation_attempted'] and not report['actuation_acknowledged']
    assert state['attempt']==1
    with pytest.raises(ValueError,match='Existing report'):
        runner.main()
    assert state['attempt']==1


def test_v4_preflight_failure_never_touches_device(monkeypatch,tmp_path):
    out=setup(monkeypatch,tmp_path,preflight_fail=True)
    assert runner.main()==1
    report=json.loads(out.read_text())
    assert report['status']=='failed_before_actuation'
    assert not report['actuation_attempted']
    assert state['attempt']==0


def test_v4_unauthorized_target_rejected_before_IO(monkeypatch,tmp_path):
    out=setup(monkeypatch,tmp_path)
    monkeypatch.setattr(sys,'argv',[x if x!='30' else '25' for x in sys.argv])
    with pytest.raises(SystemExit):runner.main()
    assert state['attempt']==0 and not out.exists()
