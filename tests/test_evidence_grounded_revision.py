"""Domain-independent evidence-grounded LLM retraction safety and API contracts."""
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

from agent_memory_core.evidence import evidence_from_hits, guard_retractions
from agent_memory_core.api import create_app


HIT={"memory_id":"mem-1", "subject_id":"supplier-7",
     "predicate":"settlement_period","value":{"months":1}}
CANDIDATE={"subject_id":"supplier-7","predicate":"settlement_period",
           "value":{"reason":"revised"},"operation":"RETRACT","confidence":0.93,
           "expected_value":{"months":1},"expected_memory_id":"mem-1"}


def test_evidence_references_are_exact_and_not_vocabulary_bound():
    ground=evidence_from_hits([SimpleNamespace(**HIT)])
    got=guard_retractions([CANDIDATE],ground)
    assert got[0]["operation"]=="RETRACT"
    assert "expected_memory_id" not in got[0]
    assert got[0]["expected_value"]=={"months":1}


@pytest.mark.parametrize("change",[
    {"expected_memory_id":"not-in-evidence"},
    {"subject_id":"tenant-elsewhere"},
    {"predicate":"bank_balance"},
    {"expected_value":{"months":3}},
    {"subject_name":"supplier-7"},
])
def test_model_cannot_invent_or_broaden_withdrawal(change):
    proposed={**CANDIDATE,**change}
    with pytest.raises(ValueError):
        guard_retractions([proposed],[HIT])


def test_model_cannot_delete_if_memory_not_retrieved():
    with pytest.raises(ValueError):
        guard_retractions([CANDIDATE],[])


def test_new_domain_fact_can_be_created_without_existing_vocabulary():
    novel={"subject_id":"rail-signal-7","predicate":"maintenance_window",
           "value":{"days":45},"confidence":0.9}
    assert guard_retractions([novel],[])==[novel]


class DummyMemory:
    def __init__(self):self.forgotten=[]
    def predicate_cardinality(self,**kw):return "one"
    def forget_many(self, *, tenant_id, subject_id=None, predicate=None,
                    source_id=None, expected_value=None):
        self.forgotten.append({"tenant_id":tenant_id,"subject_id":subject_id,
                               "predicate":predicate,"source_id":source_id,
                               "expected_value":expected_value})
        return ["mem-1"]
    def close(self):pass

class DummySearch:
    def __init__(self,hits):self.hits=hits;self.calls=[];self.closed=False
    def search_detailed(self,**kw):
        self.calls.append(kw)
        return SimpleNamespace(hits=tuple(SimpleNamespace(**h) for h in self.hits))
    def close(self):self.closed=True

class DummyExtractor:
    def __init__(self,proposed):self.proposed=proposed;self.seen=None
    def __call__(self,text):return []
    def extract_with_evidence(self,text,context_turns,memory_evidence):
        self.seen=(text,context_turns,memory_evidence)
        return self.proposed


KEY="local-test-key-with-32-characters-at-least"
AUTH={"Authorization":"Bearer "+KEY}


def run_request(proposed,hits,*,ground=True):
    memory=DummyMemory()
    search=DummySearch(hits)
    extractor=DummyExtractor(proposed)
    app=create_app(api_key=KEY,tenant_id="tenant-a",store_factory=lambda:memory,
                   search_factory=lambda:search,
                   memory_extractor_factory=lambda _:extractor)
    response=TestClient(app).post("/v1/memories/process",
        headers=AUTH,json={"text":"供应商结算取消按月",
                           "source_id":"dialogue-1",
                           "ground_with_memory":ground,
                           "context_turns":["供应商原来每月结算"]})
    return response,memory,search,extractor


def test_authenticated_api_retrieves_canonical_evidence_then_processes_withdrawal():
    response,memory,search,extractor=run_request([CANDIDATE],[HIT])
    assert response.status_code==200,response.text
    assert response.json()["replaced"]==["mem-1"]
    assert search.calls[0]["tenant_id"]=="tenant-a"
    assert search.calls[0]["limit"]==8
    assert search.closed
    assert extractor.seen[2]==[HIT]
    assert memory.forgotten[0]["subject_id"]=="supplier-7"
    assert memory.forgotten[0]["expected_value"]=={"months":1}


def test_api_fails_closed_without_verified_evidence():
    response,memory,search,_=run_request([CANDIDATE],[])
    assert response.status_code==422
    assert memory.forgotten==[]
    assert search.closed


def test_api_rejects_wrong_candidate_before_any_withdrawal():
    bad={**CANDIDATE,"expected_memory_id":"invented"}
    response,memory,search,_=run_request([bad],[HIT])
    assert response.status_code==422
    assert memory.forgotten==[]


def test_api_rejects_untrusted_candidates_combined_with_grounding():
    app=create_app(api_key=KEY,tenant_id="tenant-a",
                   store_factory=DummyMemory)
    response=TestClient(app).post("/v1/memories/process",headers=AUTH,
        json={"text":"test","source_id":"s1","candidates":[],
              "ground_with_memory":True})
    assert response.status_code==422
