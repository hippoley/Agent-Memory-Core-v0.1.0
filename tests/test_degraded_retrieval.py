from datetime import datetime,timezone

class FakeConn:
    def execute(self,sql,params):
        if "SELECT id::text FROM memory_claims" in sql:
            return [("m1",)]
        if "WHERE id=ANY" in sql:
            return [("m1","room","preference",{"noise":"quiet"},"source-1")]
        raise AssertionError("unexpected SQL")

class FakeStore:
    conn=FakeConn()
    def vector_search(self,**kwargs):
        raise RuntimeError("vector index offline")
    def recall(self,**kwargs):
        if kwargs["subject_id"]=="room":
            return [{"id":"m1"}]
        return []

class BrokenGraph:
    def neighbors(self,**kwargs):
        raise ConnectionError("AGE unavailable")

def test_graph_and_vector_failure_degrade_to_sql():
    from agent_memory_core.hybrid import HybridMemorySearch
    report=HybridMemorySearch(FakeStore(),graph=BrokenGraph()).search_detailed(
        tenant_id="tenant-a",query="quiet",embedding=[0.1]*384,model_id="embed-v1",
        anchor_entity="room",valid_at=datetime.now(timezone.utc))
    assert len(report.hits)==1
    assert report.hits[0].memory_id=="m1"
    assert report.hits[0].channels==("sql",)
    assert set(report.degraded_channels)=={"graph","vector"}

def test_no_optional_adapter_is_a_valid_mode():
    from agent_memory_core.hybrid import HybridMemorySearch
    report=HybridMemorySearch(FakeStore()).search_detailed(
        tenant_id="tenant-a",query="quiet",valid_at=datetime.now(timezone.utc))
    assert len(report.hits)==1
    assert report.channels_used==("sql",)
