import hashlib
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from agent_memory_core.api import create_app
from agent_memory_core.objects import ObjectStore


class FakeDB:
    def __init__(self):
        self.observations_data = {}
        self.events_data = []
        self.entities = {}
        self.edges = []
        self.embedding_rows = []
        self.memories = []
        self.source_links = []
        self.objects_owned = set()
    def close(self):
        pass
    def remember(self, **kwargs):
        from datetime import datetime, timezone
        old=kwargs.get("replaces_id")
        if old:
            for row in self.memories:
                if row["id"]==old:
                    row["superseded_at"]=datetime.now(timezone.utc)
        mid=f"memory-{len(self.memories)+1}"
        self.memories.append({"id":mid,"tenant_id":kwargs["tenant_id"],
            "subject_id":kwargs["subject_id"],"predicate":kwargs["predicate"],
            "value":kwargs["value"],"source_id":kwargs["source_id"],
            "superseded_at":None})
        return mid
    def recall(self, *, tenant_id, subject_id, predicate=None, valid_at=None, known_at=None):
        return [row for row in reversed(self.memories)
            if row["tenant_id"]==tenant_id and row["subject_id"]==subject_id
            and (predicate is None or row["predicate"]==predicate)
            and row["superseded_at"] is None]
    def link_claim_source(self, **kwargs):
        self.source_links.append(kwargs)
        return True
    def register_object(self, *, tenant_id, digest, byte_size, content_type):
        self.objects_owned.add((tenant_id,digest))
    def object_accessible(self, *, tenant_id, digest):
        return (tenant_id,digest) in self.objects_owned
    def record_observation(self, **kwargs):
        key=(kwargs["tenant_id"],kwargs["device_id"],kwargs["source_event_id"])
        old=self.observations_data.get(key)
        value=(kwargs["observed_at"],kwargs["property"],kwargs["value"],kwargs["unit"],kwargs["source_id"])
        if old is not None:
            if old != value:
                raise ValueError("Conflicting observation with same source event ID")
            return False
        self.observations_data[key]=value
        self.events_data.append({"event_id":kwargs["source_event_id"],
            "event_type":"memory.observation.recorded","payload":{"device_id":kwargs["device_id"]},
            "recorded_at":kwargs["observed_at"]})
        return True
    def observations(self, *, tenant_id, device_id, property, limit=100, start_at=None, end_at=None):
        rows=[]
        for (t,d,e),v in self.observations_data.items():
            if (t,d)==(tenant_id,device_id) and v[1]==property:
                rows.append({"source_event_id":e,"observed_at":v[0],"property":v[1],
                             "measured_value":v[2],"unit":v[3],"source_id":v[4]})
        if start_at is not None:
            rows=[r for r in rows if r["observed_at"] >= start_at]
        if end_at is not None:
            rows=[r for r in rows if r["observed_at"] < end_at]
        return rows[:limit]
    def aggregate_observations(self, *, tenant_id, device_id, property, start_at, end_at):
        import statistics
        values=[v[2] for (t,d,_),v in self.observations_data.items()
                if t==tenant_id and d==device_id and v[1]==property
                and v[0]>=start_at and v[0]<end_at]
        return {"count":len(values),"min":min(values) if values else None,
                "max":max(values) if values else None,
                "avg":statistics.mean(values) if values else None,
                "start_at":start_at,"end_at":end_at}
    def upsert_entity(self, **kwargs):
        self.entities[kwargs["entity_id"]]=kwargs
    def resolve_entity(self, *, tenant_id, name):
        matches=[v for v in self.entities.values()
                 if v["tenant_id"]==tenant_id and
                 (v["canonical_name"].lower()==name.lower() or
                  any(a.lower()==name.lower() for a in v["aliases"]))]
        if len(matches)>1:
            raise ValueError("Ambiguous entity alias")
        if not matches:
            return None
        return {"entity_id":matches[0]["entity_id"],"canonical_name":matches[0]["canonical_name"]}
    def add_edge(self, **kwargs):
        self.edges.append(kwargs)
        return "edge-"+str(len(self.edges))
    def store_embedding(self, **kwargs):
        self.embedding_rows.append(kwargs)
    def sources_for_memory(self, **kwargs):
        return [{"source_id":"conversation-1","trust_level":"unverified"}]
    def events_after(self, *, tenant_id, after=None, limit=100):
        if tenant_id != "tenant-a":
            return []
        rows=self.events_data
        if after is not None:
            ids=[i for i,row in enumerate(rows) if row["event_id"]==after]
            if not ids:
                raise ValueError("Unknown event cursor for this tenant")
            rows=rows[ids[0]+1:]
        return rows[:limit]


def make_client(tmp_path):
    key="test-api-key-with-strong-example-length"
    db=FakeDB()
    app=create_app(api_key=key,tenant_id="tenant-a",
        store_factory=lambda:db,
        object_store_factory=lambda:ObjectStore(tmp_path/"objects"),
        capability_flags={"objects":True,"semantic":True})
    return TestClient(app), {"Authorization":"Bearer "+key}


def test_object_upload_download_hash_and_auth(tmp_path):
    client,headers=make_client(tmp_path)
    data=b"floorplan-evidence"
    put=client.put("/v1/objects",content=data,headers=headers)
    assert put.status_code==201
    digest=hashlib.sha256(data).hexdigest()
    assert put.headers["x-content-sha256"]==digest
    assert put.json()=={"sha256":digest,"size":len(data)}
    got=client.get("/v1/objects/"+digest,headers=headers)
    assert got.status_code==200
    assert got.content==data
    assert got.headers["x-content-sha256"]==digest
    assert client.get("/v1/objects/"+digest).status_code==401


def test_observation_write_is_idempotent_and_queryable(tmp_path):
    client,headers=make_client(tmp_path)
    body={"device_id":"window-1","source_event_id":"event-001",
          "observed_at":"2026-11-09T10:00:00+08:00","property":"angle",
          "value":17.5,"unit":"degree","source_id":"gateway-1"}
    first=client.post("/v1/observations",json=body,headers=headers)
    second=client.post("/v1/observations",json=body,headers=headers)
    assert first.status_code==201 and first.json()["duplicate"] is False
    assert second.status_code==200 and second.json()["duplicate"] is True
    result=client.get("/v1/observations/window-1?property=angle",headers=headers)
    assert result.status_code==200
    assert result.json()["items"][0]["source_event_id"]=="event-001"
    assert result.json()["items"][0]["measured_value"]==17.5


def test_observation_conflicting_event_id_is_rejected(tmp_path):
    client,headers=make_client(tmp_path)
    body={"device_id":"window-1","source_event_id":"event-001",
          "observed_at":"2026-11-09T10:00:00+08:00","property":"angle",
          "value":17.5,"unit":"degree","source_id":"gateway-1"}
    assert client.post("/v1/observations",json=body,headers=headers).status_code==201
    body["value"]=35.0
    assert client.post("/v1/observations",json=body,headers=headers).status_code==409


def test_openapi_includes_object_and_observation_contracts(tmp_path):
    client,headers=make_client(tmp_path)
    spec=client.get("/openapi.json").json()
    assert "/v1/objects" in spec["paths"]
    assert "/v1/objects/{digest}" in spec["paths"]
    assert "/v1/observations" in spec["paths"]
    assert "/v1/observations/{device_id}" in spec["paths"]


class FakeS3Body:
    def __init__(self, data):
        self.data=data
    def read(self):
        return self.data


class FakeS3:
    def __init__(self):
        self.objects={}
    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"],kwargs["Key"])]=kwargs["Body"]
    def get_object(self, **kwargs):
        return {"Body":FakeS3Body(self.objects[(kwargs["Bucket"],kwargs["Key"])])}


def test_s3_object_adapter_content_addressing_and_integrity():
    from agent_memory_core.objects import S3ObjectStore
    client=FakeS3()
    store=S3ObjectStore(bucket="memory-test",client=client)
    payload=b"original-evidence"
    digest=store.put(payload)
    assert store.get(digest)==payload
    assert ("memory-test",f"sha256/{digest[:2]}/{digest}") in client.objects
    client.objects[("memory-test",f"sha256/{digest[:2]}/{digest}")]=b"tampered"
    with pytest.raises(IOError):
        store.get(digest)


def test_durable_event_replay_uses_cursor_and_auth(tmp_path):
    client,headers=make_client(tmp_path)
    for event_id in ("event-1","event-2"):
        body={"device_id":"window-1","source_event_id":event_id,
              "observed_at":"2026-11-09T10:00:00+08:00","property":"angle",
              "value":17.5,"unit":"degree","source_id":"gateway-1"}
        assert client.post("/v1/observations",json=body,headers=headers).status_code==201
    first=client.get("/v1/events?limit=1",headers=headers)
    assert first.status_code==200
    assert len(first.json()["items"])==1
    cursor=first.json()["next_cursor"]
    second=client.get("/v1/events",params={"after":cursor,"limit":10},headers=headers)
    assert [e["event_id"] for e in second.json()["items"]]==["event-2"]
    assert client.get("/v1/events").status_code==401


def test_sse_stream_emits_event_id_and_replay_payload(tmp_path):
    client,headers=make_client(tmp_path)
    body={"device_id":"window-1","source_event_id":"event-stream-1",
          "observed_at":"2026-11-09T10:00:00+08:00","property":"angle",
          "value":35.0,"unit":"degree","source_id":"gateway-1"}
    assert client.post("/v1/observations",json=body,headers=headers).status_code==201
    with client.stream("GET","/v1/events/stream?max_seconds=1",headers=headers) as response:
        assert response.status_code==200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines=response.iter_lines()
        assert next(lines)=="id: event-stream-1"
        assert next(lines)=="event: memory.observation.recorded"
        assert next(lines).startswith("data: ")


def test_graph_and_vector_write_apis(tmp_path):
    client,headers=make_client(tmp_path)
    entity={"entity_id":"bedroom-1","entity_type":"room",
            "canonical_name":"Main Bedroom","aliases":["primary bedroom"],
            "attributes":{"floor":1}}
    assert client.post("/v1/entities",json=entity,headers=headers).status_code==201
    resolved=client.get("/v1/entities/resolve?name=PRIMARY%20BEDROOM",headers=headers)
    assert resolved.status_code==200
    assert resolved.json()["entity_id"]=="bedroom-1"
    edge={"from_entity":"bedroom-1","predicate":"HAS_WINDOW","to_entity":"window-1"}
    assert client.post("/v1/edges",json=edge,headers=headers).status_code==201
    embedding={"model_id":"bge-small-zh-v1.5","embedding":[0.01]*512}
    response=client.put("/v1/memories/memory-1/embedding",json=embedding,headers=headers)
    assert response.status_code==200


def test_memory_provenance_sources_are_exposed(tmp_path):
    client,headers=make_client(tmp_path)
    response=client.get("/v1/memories/memory-1/sources",headers=headers)
    assert response.status_code==200
    assert response.json()["sources"][0]["source_id"]=="conversation-1"


def test_observation_event_time_aggregation_api(tmp_path):
    client,headers=make_client(tmp_path)
    for event_id,stamp,value in [
        ("evt-a","2026-11-09T10:00:00+08:00",10.0),
        ("evt-b","2026-11-09T11:00:00+08:00",20.0),
        ("evt-c","2026-11-09T12:00:00+08:00",30.0),
    ]:
        body={"device_id":"sensor-1","source_event_id":event_id,
              "observed_at":stamp,"property":"co2","value":value,
              "unit":"ppm","source_id":"sensor-gateway"}
        assert client.post("/v1/observations",json=body,headers=headers).status_code==201
    response=client.get(
        "/v1/observations/sensor-1/aggregate",
        params={"property":"co2","start_at":"2026-11-09T10:00:00+08:00",
                "end_at":"2026-11-09T12:00:00+08:00"},headers=headers)
    assert response.status_code==200,response.text
    assert response.json()["aggregate"]["count"]==2
    assert response.json()["aggregate"]["min"]==10.0
    assert response.json()["aggregate"]["max"]==20.0
    assert response.json()["aggregate"]["avg"]==15.0


def test_memory_processing_endpoint_deduplicates_and_versions_facts(tmp_path):
    client,headers=make_client(tmp_path)
    response=client.post("/v1/memories/process",headers=headers,json={
        "text":"The bedroom should be quiet, now prefer normal noise.",
        "source_id":"conversation:session-42",
        "confidence_threshold":0.65,
        "candidates":[
            {"subject_id":"bedroom-1","predicate":"noise_preference",
             "value":{"level":"quiet"},"confidence":0.99},
            {"subject_id":"bedroom-1","predicate":"noise_preference",
             "value":{"level":"quiet"},"confidence":0.97},
            {"subject_id":"bedroom-1","predicate":"noise_preference",
             "value":{"level":"normal"},"confidence":0.91},
            {"subject_id":"bedroom-1","predicate":"noise_preference",
             "value":{"level":"loud"},"confidence":0.2}
        ]})
    assert response.status_code==200,response.text
    body=response.json()
    assert len(body["created"])==2
    assert body["duplicates"]==1
    assert body["rejected"]==1
    assert len(body["replaced"])==1


def test_memory_processing_rejects_missing_provenance(tmp_path):
    client,headers=make_client(tmp_path)
    response=client.post("/v1/memories/process",headers=headers,json={
        "text":"hello","source_id":"","candidates":[
            {"subject_id":"room-1","predicate":"p","value":{"x":1}}]})
    assert response.status_code==422


def test_object_download_requires_tenant_ownership(tmp_path):
    key_a="tenant-a-key-with-more-than-24-characters"
    key_b="tenant-b-key-with-more-than-24-characters"
    db=FakeDB()
    object_root=tmp_path/"shared-objects"
    app_a=create_app(api_key=key_a,tenant_id="tenant-a",
        store_factory=lambda:db,object_store_factory=lambda:ObjectStore(object_root))
    app_b=create_app(api_key=key_b,tenant_id="tenant-b",
        store_factory=lambda:db,object_store_factory=lambda:ObjectStore(object_root))
    client_a=TestClient(app_a)
    client_b=TestClient(app_b)
    uploaded=client_a.put("/v1/objects",headers={"Authorization":"Bearer "+key_a},
                          content=b"private tenant evidence")
    assert uploaded.status_code==201,uploaded.text
    digest=uploaded.json()["sha256"]
    assert client_a.get("/v1/objects/"+digest,
        headers={"Authorization":"Bearer "+key_a}).status_code==200
    denied=client_b.get("/v1/objects/"+digest,
        headers={"Authorization":"Bearer "+key_b})
    assert denied.status_code==404


def test_memory_claim_cannot_reference_another_tenants_object(tmp_path):
    client,headers=make_client(tmp_path)
    other_key="tenant-b-key-with-more-than-24-characters"
    shared_db=FakeDB()
    other_app=create_app(api_key=other_key,tenant_id="tenant-b",
        store_factory=lambda:shared_db,
        object_store_factory=lambda:ObjectStore(tmp_path/"objects"))
    other=TestClient(other_app)
    payload=b"tenant-b-private-evidence"
    uploaded=other.put("/v1/objects",headers={"Authorization":"Bearer "+other_key},
                       content=payload)
    assert uploaded.status_code==201
    digest=uploaded.json()["sha256"]
    claim=client.post("/v1/memories",headers=headers,json={
        "subject_id":"room-1","predicate":"layout",
        "value":{"room":"bedroom"},"source_id":"conversation:tenant-a",
        "valid_from":"2026-11-09T00:00:00Z","object_sha256":digest,
    })
    assert claim.status_code==422
    assert shared_db.memories==[]
