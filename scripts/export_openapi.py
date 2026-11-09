"""Export Swagger/OpenAPI from the actual FastAPI app without connecting to DB.

Usage:
    python scripts/export_openapi.py docs/openapi.generated.json
"""
import json
import sys
from pathlib import Path
from agent_memory_core.api import create_app

def main():
    path=Path(sys.argv[1]) if len(sys.argv)>1 else Path("docs/openapi.generated.json")
    app=create_app(api_key="offline-documentation-secret-with-32-chars",
                   tenant_id="documentation-only")
    spec=app.openapi()
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(spec,ensure_ascii=False,indent=2,sort_keys=True)+"\n",
                    encoding="utf-8")
    print(f"Wrote {path}: OpenAPI {spec['openapi']} ({len(spec['paths'])} paths)")

if __name__=="__main__":
    main()
