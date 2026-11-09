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
    def close(self):
        pass
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
    def observations(self, *, tenant_id, device_id, property, limit=100):
        rows=[]
        for (t,d,e),v in self.observations_data.items():
            if (t,d)==(tenant_id,device_id) and v[1]==property:
                rows.append({"source_event_id":e,"observed_at":v[0],"property":v[1],
                             "measured_value":v[2],"unit":v[3],"source_id":v[4]})
        return rows[:limit]
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
        capability_flags={"objects":True})
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
