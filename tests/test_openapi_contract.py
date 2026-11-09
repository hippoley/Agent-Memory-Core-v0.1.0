"""Public OpenAPI/Swagger shape and security regression tests."""
import json
from pathlib import Path
import pytest
pytest.importorskip("fastapi")
from agent_memory_core.api import create_app

EXPECTED={
    "/v1/health":{"get"},
    "/v1/ready":{"get"},
    "/v1/capabilities":{"get"},
    "/v1/memories":{"post"},
    "/v1/memories/{subject_id}":{"get"},
    "/v1/search":{"post"},
    "/v1/objects":{"put"},
    "/v1/objects/{digest}":{"get"},
    "/v1/observations":{"post"},
    "/v1/observations/{device_id}":{"get"},
    "/v1/events":{"get"},
}

def spec():
    return create_app(
        api_key="documentation-only-key-with-32-or-more-characters",
        tenant_id="contract-tests").openapi()

def test_runtime_swagger_is_openapi_and_complete():
    document=spec()
    assert document["openapi"].startswith("3.")
    assert {path:set(ops) for path,ops in document["paths"].items()}==EXPECTED
    assert document["info"]["title"]=="Agent Memory Core API"
    assert "/docs" not in document["paths"]

def test_every_nonpublic_operation_has_bearer_security():
    document=spec()
    schemes=document["components"]["securitySchemes"]
    bearer=[name for name,definition in schemes.items()
            if definition.get("type")=="http" and definition.get("scheme")=="bearer"]
    assert bearer
    for path,operations in document["paths"].items():
        for operation in operations.values():
            if path=="/v1/health":
                continue
            assert any(b in item for item in operation.get("security",[]) for b in bearer), (path,operation)

def test_response_and_request_models_are_described():
    document=spec()
    for path,method,success in [
        ("/v1/health","get","200"),("/v1/ready","get","200"),
        ("/v1/capabilities","get","200"),("/v1/memories","post","201"),
        ("/v1/memories/{subject_id}","get","200"),("/v1/search","post","200")
    ]:
        response=document["paths"][path][method]["responses"][success]
        assert "application/json" in response["content"], (path,method)
        assert "schema" in response["content"]["application/json"], (path,method)
    claim_schema=document["components"]["schemas"]["Claim"]
    assert {"subject_id","predicate","value","source_id","valid_from"}<=set(claim_schema["required"])
    search_schema=document["components"]["schemas"]["SearchRequest"]
    assert search_schema["properties"]["embedding"]["description"]

def test_no_unimplemented_operations_exposed():
    exposed=set(spec()["paths"])
    for prefix in ("/v1/workflows","/v1/knowledge-bases",
                   "/v1/graph","/v1/documents","/v1/stream"):
        assert not any(p.startswith(prefix) for p in exposed)

def test_generated_swagger_export_matches_live_contract():
    import subprocess,sys,tempfile
    with tempfile.TemporaryDirectory() as d:
        output=Path(d)/"openapi.json"
        subprocess.run([sys.executable,"scripts/export_openapi.py",str(output)],
                       check=True,capture_output=True,text=True)
        exported=json.loads(output.read_text(encoding="utf-8"))
        assert exported==spec()
