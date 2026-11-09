"""Default single-tenant REST deployment wiring.

Vector search uses caller-supplied embeddings. AGE projection is opt-in with
MEMORY_ENABLE_AGE=1. This module does not start Temporal or an embedding model.
"""
import os

from .api import create_app
from .objects import ObjectStore, S3ObjectStore
from .local_extractor import LocalOpenAICompatibleExtractor



def local_extractor_factory():
    base_url = os.getenv("MEMORY_LOCAL_MODEL_URL")
    model = os.getenv("MEMORY_LOCAL_MODEL")
    if not base_url and not model:
        return None
    if not base_url or not model:
        raise RuntimeError("Set both MEMORY_LOCAL_MODEL_URL and MEMORY_LOCAL_MODEL")
    return LocalOpenAICompatibleExtractor(
        base_url=base_url,
        model=model,
        timeout=float(os.getenv("MEMORY_LOCAL_MODEL_TIMEOUT", "30")),
        api_key=os.getenv("MEMORY_LOCAL_MODEL_API_KEY") or None,
    )


text_extractor = local_extractor_factory()

def search_factory():
    from .postgres import PostgresMemory
    from .hybrid import HybridMemorySearch

    db = PostgresMemory(os.environ["MEMORY_DSN"])
    graph = None
    if os.getenv("MEMORY_ENABLE_AGE", "0") == "1":
        from .age_graph import AgeProjection
        try:
            graph = AgeProjection(db.conn)
            graph.initialize()
        except Exception:
            # A failed optional graph initialization must not prevent canonical
            # SQL and vector retrieval. Graph failure appears in diagnostics.
            class UnavailableGraph:
                def neighbors(self, **kwargs):
                    raise RuntimeError("Apache AGE graph adapter is unavailable")
            graph = UnavailableGraph()
    return HybridMemorySearch(db, graph=graph)


def object_store_factory():
    backend = os.getenv("MEMORY_OBJECT_BACKEND", "local").lower()
    if backend == "local":
        root = os.getenv("MEMORY_OBJECT_ROOT", "./data/objects")
        return ObjectStore(root)
    if backend == "s3":
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("Install the 's3' extra: pip install -e '.[s3]'") from exc
        bucket = os.environ["MEMORY_S3_BUCKET"]
        client = boto3.client(
            "s3",
            endpoint_url=os.getenv("MEMORY_S3_ENDPOINT_URL") or None,
            region_name=os.getenv("MEMORY_S3_REGION", "us-east-1"),
            aws_access_key_id=os.getenv("MEMORY_S3_ACCESS_KEY_ID") or None,
            aws_secret_access_key=os.getenv("MEMORY_S3_SECRET_ACCESS_KEY") or None,
        )
        return S3ObjectStore(bucket=bucket, client=client)
    raise RuntimeError("MEMORY_OBJECT_BACKEND must be 'local' or 's3'")



async def temporal_client_factory():
    try:
        from temporalio.client import Client
    except ImportError as exc:
        raise RuntimeError("Install agent-memory-core[temporal]") from exc
    address=os.getenv("MEMORY_TEMPORAL_ADDRESS")
    if not address:
        raise RuntimeError("MEMORY_TEMPORAL_ADDRESS is not configured")
    return await Client.connect(
        address,
        namespace=os.getenv("MEMORY_TEMPORAL_NAMESPACE","default"),
    )


flags = {
    "semantic": os.getenv("MEMORY_ENABLE_VECTOR", "0") == "1",
    "graph": os.getenv("MEMORY_ENABLE_AGE", "0") == "1",
    "objects": True,
    "temporal": bool(os.getenv("MEMORY_TEMPORAL_ADDRESS")),
    "local_extraction": text_extractor is not None,
}

app = create_app(text_extractor=text_extractor, search_factory=search_factory,
                 object_store_factory=object_store_factory,
                 capability_flags=flags,
                 temporal_client_factory=temporal_client_factory if flags["temporal"] else None,
                 temporal_task_queue=os.getenv("MEMORY_TEMPORAL_TASK_QUEUE","agent-memory-core"))
