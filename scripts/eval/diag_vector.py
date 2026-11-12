"""Smoke the vector channel: write a claim, search by paraphrase."""
import json
import httpx

BASE = "http://127.0.0.1:8767"
KEY = "lme-eval-key-0123456789abcdef"
client = httpx.Client(base_url=BASE, timeout=60,
                      headers={"Authorization": f"Bearer {KEY}"})

# 1) write two claims, one about camping duration, one unrelated
for body in (
    {"subject_id": "user", "predicate": "completed_camping_trip",
     "value": {"location": "Utah", "days": 3},
     "source_id": "diag", "valid_from": "2023-04-01T00:00:00+00:00"},
    {"subject_id": "user", "predicate": "owns_pet",
     "value": {"kind": "cat"},
     "source_id": "diag", "valid_from": "2023-04-01T00:00:00+00:00"},
):
    r = client.post("/v1/memories", json=body)
    print("write", r.status_code, r.json())

# 2) semantic paraphrase: none of these words appears in the claim text
for query in ("how many days did I spend camping in the states",
              "what animal do I keep at home"):
    r = client.post("/v1/search", json={"query": query, "limit": 5})
    data = r.json()
    print(f"\nQ: {query}")
    print("channels:", data.get("channels_used"), "degraded:", data.get("degraded_channels"))
    for hit in data["items"]:
        print("  ", hit["predicate"], json.dumps(hit["value"], ensure_ascii=False)[:60])
