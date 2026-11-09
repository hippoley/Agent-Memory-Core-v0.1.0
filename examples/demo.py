from agent_memory_core import MemoryStore, ObjectStore
from pathlib import Path

root = Path(".demo")
store = MemoryStore(root / "memory.sqlite") if root.exists() else None
if store is None:
    root.mkdir(exist_ok=True)
    store = MemoryStore(root / "memory.sqlite")
objects = ObjectStore(root / "objects")
sha = objects.put(b'{"window_id":"window-1","reported_angle":35}')
mid = store.remember(tenant_id="home-1",subject_id="window-1",predicate="road_noise_preference",
                     value="avoid opening at night",source_id="utterance-42",
                     valid_from="2026-11-01T00:00:00Z",object_sha256=sha)
print("memory:",mid)
print("current:",store.recall(tenant_id="home-1",subject_id="window-1"))
print("evidence:",objects.get(sha))
store.close()
