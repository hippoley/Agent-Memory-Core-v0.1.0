"""Real PG proof: unknown-industry canonical memory -> search -> model proposal -> guarded withdraw."""
import os
from uuid import uuid4
from types import SimpleNamespace

import pytest

pytestmark=pytest.mark.integration


@pytest.mark.parametrize("atomic_batch", [False, True])
def test_grounded_revision_uses_real_postgres_and_keeps_history(atomic_batch):
    dsn=os.getenv("MEMORY_TEST_DSN")
    if not dsn:
        pytest.skip("Requires isolated PostgreSQL AGE/pgvector test service")
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_memory_core.postgres import PostgresMemory
    from agent_memory_core.hybrid import HybridMemorySearch
    from agent_memory_core.api import create_app

    tenant="evidence-"+uuid4().hex
    token="local-integration-test-api-key-over-32-chars"
    s=PostgresMemory(dsn)
    try:
        s.migrate()
        new_id,_,_,_=s.apply_candidate(
            tenant_id=tenant,subject_id="supplier-7",
            predicate="settlement_period",value={"months":1},
            source_id="contract-v1",confidence=1.0)
    finally:
        s.close()

    class LLM:
        def __init__(self):self.evidence=None
        def __call__(self,text):return []
        def extract_with_evidence(self,text,context_turns,memory_evidence):
            self.evidence=memory_evidence
            assert len(memory_evidence)==1,memory_evidence
            old=memory_evidence[0]
            return [{"subject_id":old["subject_id"],"predicate":old["predicate"],
                     "value":{"reason":"period_changed"},
                     "operation":"RETRACT","confidence":0.99,
                     "expected_memory_id":old["memory_id"],
                     "expected_value":old["value"]},
                    {"subject_id":old["subject_id"],"predicate":old["predicate"],
                     "value":{"months":3},"operation":"UPSERT",
                     "confidence":0.99}]
    model=LLM()
    app=create_app(
        api_key=token,tenant_id=tenant,
        store_factory=lambda:PostgresMemory(dsn),
        search_factory=lambda:HybridMemorySearch(PostgresMemory(dsn)),
        memory_extractor_factory=lambda _:model)
    client=TestClient(app)
    response=client.post("/v1/memories/process",
        headers={"Authorization":"Bearer "+token},json={
        "text":"supplier-7 settlement period now quarterly, not monthly",
        "source_id":"dialogue-turn-2","ground_with_memory":True,
        "atomic_batch":atomic_batch,
        "context_turns":["supplier-7 settles every month"]})
    assert response.status_code==200,response.text
    assert response.json()["replaced"]==[new_id],response.json()
    if atomic_batch:
        assert response.json()["atomic_batch"] is True
    assert model.evidence[0]["memory_id"]==new_id

    verify=PostgresMemory(dsn)
    try:
        active=verify.recall(tenant_id=tenant,subject_id="supplier-7")
        assert len(active)==1
        assert active[0]["value"]=={"months":3}
        # Even if a stale reference somehow has the NEW value, its OLD id
        # cannot withdraw the newly created canonical claim.
        stale=verify.forget_many(
            tenant_id=tenant,subject_id="supplier-7",predicate="settlement_period",
            expected_value={"months":3},expected_memory_id=new_id,
            source_id="stale-revision")
        assert stale==[]
        assert verify.recall(tenant_id=tenant,subject_id="supplier-7")[0]["value"]=={"months":3}
        historical=verify.list_claims(tenant_id=tenant,
                           subject_id="supplier-7",include_superseded=True)
        assert any(x["id"]==new_id for x in historical)
        assert verify.recall(tenant_id="other-tenant",
                             subject_id="supplier-7")==[]
    finally:
        verify.close()
