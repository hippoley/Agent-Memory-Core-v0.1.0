"""Pure unit tests for safe Cypher generation and bounds.

These DO NOT constitute a running Apache AGE integration test.
"""
import pytest
from agent_memory_core.age_graph import edge_upsert_cypher, graph_neighbors_cypher


def test_cypher_escapes_user_properties_and_uses_fixed_labels():
    stmt = edge_upsert_cypher(
        tenant_id='tenant" } MATCH (evil) //',
        edge_id='edge-1',
        from_entity='room\\newline\n"evil"',
        predicate='CONNECTED_TO',
        to_entity='window-2',
    )
    assert 'MERGE (a:MemoryEntity' in stmt
    assert 'MEMORY_REL' in stmt
    assert '\\" } MATCH' in stmt
    assert '\\n' in stmt
    assert 'CONNECTED_TO' in stmt


def test_graph_bounded_traversal():
    assert "RETURN DISTINCT" in graph_neighbors_cypher(tenant_id="t", from_entity="a", hops=2)
    with pytest.raises(ValueError):
        graph_neighbors_cypher(tenant_id="t", from_entity="a", hops=3)


def test_no_dynamic_edge_type():
    stmt = edge_upsert_cypher(tenant_id="t",edge_id="id",from_entity="a",
                              predicate="NOT_A_LABEL",to_entity="b")
    assert "r.predicate" in stmt
    assert "[:NOT_A_LABEL]" not in stmt

def test_delete_cypher_escaped_fixed_label():
    from agent_memory_core.age_graph import edge_delete_cypher
    stmt=edge_delete_cypher(tenant_id='t"\\n',edge_id='id} DELETE ALL')
    assert "MATCH ()-[r:MEMORY_REL" in stmt
    assert "DELETE r RETURN r" in stmt
    assert 'id} DELETE ALL' in stmt
