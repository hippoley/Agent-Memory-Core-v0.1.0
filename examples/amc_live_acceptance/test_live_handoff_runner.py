import importlib.util
from pathlib import Path
import pytest

p = Path(__file__).with_name('run_live_handoff.py')
spec=importlib.util.spec_from_file_location('live_handoff',p)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_endpoints_allow_separate_secure_origins():
    m.validate_endpoints('http://127.0.0.1:8000/v1/chat/completions',
                         'https://control.local/command','https://observer.local/observe')

@pytest.mark.parametrize('act,obs',[
    ('http://control.local/command','https://observer.local/observe'),
    ('https://control.local/command','https://control.local/observe'),
    ('https://x:password@control.local/command','https://observer.local/observe'),
])
def test_insecure_or_nonseparate_endpoints_fail(act,obs):
    with pytest.raises(ValueError):
        m.validate_endpoints('http://localhost:8000/v1/chat/completions',act,obs)

def test_observer_must_supply_device_and_source():
    assert m.response_property({'device_id':'bed','temperature':26,'source':'hardware'},device_id='bed',property_name='temperature') == 26
    with pytest.raises(ValueError):
        m.response_property({'device_id':'bed','temperature':26},device_id='bed',property_name='temperature')
    with pytest.raises(ValueError):
        m.response_property({'device_id':'other','temperature':26,'source':'hardware'},device_id='bed',property_name='temperature')
