from datetime import datetime, timezone
import pytest
from agent_memory_core import MemoryStore, MemoryConflict, ObjectStore


def test_temporal_correction_and_as_known_at(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    a = store.remember(tenant_id="home-a", subject_id="window-1", predicate="night_preference",
                       value="open", source_id="utterance-1", valid_from="2026-11-01T00:00:00Z",
                       recorded_at="2026-11-01T10:00:00Z")
    b = store.remember(tenant_id="home-a", subject_id="window-1", predicate="night_preference",
                       value="closed", source_id="utterance-2", valid_from="2026-11-02T00:00:00Z",
                       recorded_at="2026-11-02T10:00:00Z", replaces_id=a)
    past = store.recall(tenant_id="home-a", subject_id="window-1", predicate="night_preference",
                        valid_at="2026-11-01T20:00:00Z", known_at="2026-11-01T21:00:00Z")
    assert [x.id for x in past] == [a]
    now = store.recall(tenant_id="home-a", subject_id="window-1", predicate="night_preference",
                       valid_at="2026-11-03T00:00:00Z", known_at="2026-11-03T00:00:00Z")
    assert [x.id for x in now] == [b]
    assert len(store.history(tenant_id="home-a",subject_id="window-1")) == 2
    assert store.recall(tenant_id="home-b", subject_id="window-1") == []
    with pytest.raises(MemoryConflict):
        store.remember(tenant_id="home-b",subject_id="window-1", predicate="night_preference",
                       value="x",source_id="x",valid_from="2026-11-03T00:00:00Z",replaces_id=b)
    store.close()


def test_event_idempotency(tmp_path):
    s=MemoryStore(tmp_path/"m.db")
    assert s.append_event(tenant_id="t",event_id="e1",event_type="observation",payload={"angle":35})
    assert not s.append_event(tenant_id="t",event_id="e1",event_type="observation",payload={"angle":35})
    with pytest.raises(MemoryConflict):
        s.append_event(tenant_id="t",event_id="e1",event_type="observation",payload={"angle":0})
    assert len(s.events(tenant_id="t"))==1
    assert s.events(tenant_id="other")==[]


def test_object_integrity(tmp_path):
    o=ObjectStore(tmp_path/"objects")
    h=o.put(b"floorplan")
    assert o.put(b"floorplan")==h
    assert o.get(h)==b"floorplan"
    with pytest.raises(ValueError):o.get("../etc/passwd")
    f=tmp_path/"objects"/h[:2]/h
    f.write_bytes(b"corrupted")
    with pytest.raises(IOError):o.get(h)


def test_invalid_naive_time(tmp_path):
    s=MemoryStore(tmp_path/"m.db")
    with pytest.raises(ValueError):
        s.remember(tenant_id="t",subject_id="s",predicate="p",value="v",source_id="src", valid_from="2026-11-01")
