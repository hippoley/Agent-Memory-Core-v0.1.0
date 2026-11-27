"""READ-only UNKNOWN reconciliation tests, no real endpoints."""
import hashlib
import json
from pathlib import Path
import pytest
from reconcile_unknown import reconcile


class Response:
    def __init__(self,body):
        self.body=body
        self.content=json.dumps(body).encode()
    def raise_for_status(self): pass
    def json(self):return self.body


class Observer:
    def __init__(self,body):
        self.body=body
        self.gets=0
    def get(self,*args,**kw):
        self.gets+=1
        return Response(self.body)
    def post(self,*args,**kw):
        raise AssertionError('Reconciliation must never POST commands')


def setup(tmp_path,status='unknown_effect',attempted=True,phase='requires_manual_reconciliation'):
    src=tmp_path/'original.json'
    src.write_text(json.dumps({
        'schema':'amc-live-v4','run_id':'abcd1234abcd1234',
        'actuation_attempted':attempted,'status':status,'phase':phase,
        'device_id':'master-bedroom','property':'temperature','target':26}))
    return src,tmp_path/'reconcile.json'


def run(src,out,client):
    return reconcile(prior_report=src,output=out,
        model_url='http://127.0.0.1:8000/v1/chat/completions',
        command_url='https://command.example.com/command',
        observer_url='https://observer.example.com/state',
        observer_token='observer-only-token',http_client=client)


def test_match_not_causation(tmp_path):
    src,out=setup(tmp_path)
    obs=Observer({'device_id':'master-bedroom','source':'hardware','temperature':26})
    result=run(src,out,obs)
    assert result['status']=='state_matches_target'
    assert not result['causal_attribution_proven']
    assert result['device_command_retried'] is False
    assert result['original_report_sha256']==hashlib.sha256(src.read_bytes()).hexdigest()
    assert obs.gets==1


def test_differing_readback_not_failed_command(tmp_path):
    src,out=setup(tmp_path)
    result=run(src,out,Observer({'device_id':'master-bedroom','source':'hardware','temperature':24}))
    assert result['status']=='state_differs_from_target'
    assert not result['causal_attribution_proven']


def test_untrusted_observation_does_not_claim_success(tmp_path):
    src,out=setup(tmp_path)
    result=run(src,out,Observer({'device_id':'master-bedroom','temperature':26}))
    assert result['status']=='readback_unavailable'
    assert 'observed' not in result


def test_wrong_device_is_rejected(tmp_path):
    src,out=setup(tmp_path)
    result=run(src,out,Observer({'device_id':'other','source':'hardware','temperature':26}))
    assert result['status']=='readback_unavailable'


def test_replay_output_rejected_before_second_read(tmp_path):
    src,out=setup(tmp_path)
    obs=Observer({'device_id':'master-bedroom','source':'hardware','temperature':26})
    run(src,out,obs)
    with pytest.raises(FileExistsError):run(src,out,obs)
    assert obs.gets==1


def test_never_accept_unattempted_operation(tmp_path):
    src,out=setup(tmp_path,attempted=False)
    obs=Observer({'device_id':'master-bedroom','source':'hardware','temperature':26})
    with pytest.raises(ValueError,match='only for'):run(src,out,obs)
    assert obs.gets==0 and not out.exists()


def test_recover_interrupted_report_after_command_intent(tmp_path):
    src,out=setup(tmp_path,status=None,phase='actuation_attempted')
    result=run(src,out,Observer({'device_id':'master-bedroom','source':'hardware','temperature':26}))
    assert result['status']=='state_matches_target'


def test_deny_overwriting_original(tmp_path):
    src,_=setup(tmp_path)
    with pytest.raises(ValueError,match='overwrite'):run(src,src,Observer({}))
