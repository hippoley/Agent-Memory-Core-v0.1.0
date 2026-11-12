from datetime import datetime, timezone
from agent_memory_core.processing import MemoryProcessor


class FakeStore:
    def __init__(self):
        self.rows=[]
        self.embeddings=[]
        self.sources=[]
    def recall(self, *, tenant_id, subject_id, predicate, valid_at, known_at):
        return [r for r in reversed(self.rows)
                if r["tenant_id"]==tenant_id and r["subject_id"]==subject_id
                and r["predicate"]==predicate and r["superseded_at"] is None]
    def remember(self, **kwargs):
        old=kwargs.get("replaces_id")
        if old:
            for row in self.rows:
                if row["id"]==old:
                    row["superseded_at"]=datetime.now(timezone.utc)
        mid=f"memory-{len(self.rows)+1}"
        self.rows.append({"id":mid,"tenant_id":kwargs["tenant_id"],
            "subject_id":kwargs["subject_id"],"predicate":kwargs["predicate"],
            "value":kwargs["value"],"superseded_at":None})
        return mid
    def store_embedding(self, **kwargs):
        self.embeddings.append(kwargs)
    def link_claim_source(self, **kwargs):
        self.sources.append(kwargs)
        return True


def test_processor_creates_deduplicates_and_versions_conflicts():
    store=FakeStore()
    processor=MemoryProcessor(store)
    extract=lambda text:[
        {"subject_id":"room-1","predicate":"noise","value":{"level":"quiet"}},
        {"subject_id":"room-1","predicate":"noise","value":{"level":"quiet"}},
        {"subject_id":"room-1","predicate":"noise","value":{"level":"normal"}},
        {"subject_id":"room-1","predicate":"noise","value":{"level":"loud"},"confidence":0.2},
        "not-a-candidate",
    ]
    report=processor.process(tenant_id="home",text="conversation",
                             source_id="conversation-1",extractor=extract)
    assert report.created==["memory-1","memory-2"]
    assert report.duplicates==1
    assert store.sources==[{'tenant_id':'home','memory_id':'memory-1','source_id':'conversation-1','confidence':1.0}]
    assert report.replaced==["memory-1"]
    assert report.rejected==2
    active=store.recall(tenant_id="home",subject_id="room-1",
                        predicate="noise",valid_at=datetime.now(timezone.utc),
                        known_at=datetime.now(timezone.utc))
    assert len(active)==1 and active[0]["value"]=={"level":"normal"}


def test_processor_can_attach_embeddings_and_reports_projection_failure():
    store=FakeStore()
    processor=MemoryProcessor(store,embedder=lambda item:("test-model",[0.1,0.2]))
    report=processor.process(tenant_id="home",text="hello",source_id="s1",
        extractor=lambda _: [{"subject_id":"device-1","predicate":"state",
                              "value":{"on":True}}])
    assert report.created==["memory-1"]
    assert len(store.embeddings)==1
    broken=MemoryProcessor(store,embedder=lambda item:(_ for _ in ()).throw(RuntimeError("offline")))
    report=broken.process(tenant_id="home",text="hello",source_id="s2",
        extractor=lambda _: [{"subject_id":"device-2","predicate":"state",
                              "value":{"on":False}}])
    assert report.created==["memory-2"]
    assert report.embedding_failures==["memory-2"]


def test_processor_requires_provenance_and_rejects_low_confidence():
    processor=MemoryProcessor(FakeStore(),confidence_threshold=0.7)
    try:
        processor.process(tenant_id="home",text="hello",source_id="",
                          extractor=lambda _: [])
        assert False, "missing provenance must fail"
    except ValueError:
        pass
    report=processor.process(tenant_id="home",text="hello",source_id="s1",
        extractor=lambda _: [{"subject_id":"x","predicate":"p","value":{},"confidence":0.69}])
    assert report.rejected==1


def test_processor_uses_canonical_entity_resolution():
    store=FakeStore()
    resolver=lambda tenant,name: {"entity_id":"canonical-bedroom-1"} if name.lower() in {"main bedroom","bedroom"} else None
    processor=MemoryProcessor(store,entity_resolver=resolver)
    report=processor.process(tenant_id="home",text="bedroom",source_id="s1",
        extractor=lambda _: [{"subject_name":"Main Bedroom","predicate":"air_quality",
                              "value":{"target":"good"}}])
    assert report.created==["memory-1"]
    assert store.rows[0]["subject_id"]=="canonical-bedroom-1"


def test_processor_rejects_boolean_and_nonfinite_confidence():
    import math
    processor=MemoryProcessor(FakeStore(),confidence_threshold=0.0)
    report=processor.process(tenant_id="home",text="hello",source_id="s1",
        extractor=lambda _: [
            {"subject_id":"room","predicate":"noise","value":{"level":"quiet"},"confidence":True},
            {"subject_id":"room","predicate":"noise","value":{"level":"quiet"},"confidence":math.nan},
            {"subject_id":"room","predicate":"noise","value":{"level":"quiet"},"confidence":math.inf},
        ])
    assert report.created==[]
    assert report.rejected==3


def test_observed_at_backdates_the_claim_interval():
    """Replaying a historical utterance files the claim under that instant."""
    store = FakeStore()
    seen = {}
    original = store.remember

    def spy(**kwargs):
        seen.update(kwargs)
        return original(**kwargs)

    store.remember = spy
    processor = MemoryProcessor(store)
    observed = datetime(2023, 3, 15, 9, 30, tzinfo=timezone.utc)
    processor.process(
        tenant_id="tenant-1", text="I serviced my car on March 15.",
        source_id="session:2023-03-15",
        extractor=lambda _text: [{"subject_id": "user-1", "predicate": "car_service",
                                  "value": {"date": "2023-03-15"}}],
        observed_at=observed)
    assert seen["valid_from"] == observed


def test_subject_names_are_registered_once_and_reused():
    """A text extractor emits names; the first sight must not be dropped."""
    from agent_memory_core.api import _subject_resolver

    class Core:
        def __init__(self):
            self.rows = {}
            self.upserts = []

        def resolve_entity(self, *, tenant_id, name):
            return self.rows.get((tenant_id, name.casefold()))

        def upsert_entity(self, **kwargs):
            self.upserts.append(kwargs)
            self.rows[(kwargs["tenant_id"], kwargs["canonical_name"].casefold())] = {
                "entity_id": kwargs["entity_id"]}

    core = Core()
    resolve = _subject_resolver(core)
    first = resolve("t1", "my car")
    assert first["entity_id"].startswith("auto-")
    assert core.upserts[0]["entity_type"] == "concept"
    again = resolve("t1", "my car")
    assert again["entity_id"] == first["entity_id"]
    assert len(core.upserts) == 1
