"""Content-addressed local object store with atomic writes and hash verification."""
import hashlib
import os
from pathlib import Path
import tempfile


class ObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        dst = self.root / digest[:2] / digest
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            fd, tmp = tempfile.mkstemp(dir=dst.parent)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, dst)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return digest

    def get(self, digest: str) -> bytes:
        if len(digest)!=64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Invalid SHA256")
        data = (self.root / digest[:2] / digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise IOError("Object checksum mismatch")
        return data


class S3ObjectStore:
    """S3-compatible content-addressed object store (AWS S3, MinIO, etc.).

    The caller supplies a boto3-compatible client. Object keys are derived only
    from the SHA-256 digest; reads always verify content integrity.
    """
    def __init__(self, *, bucket: str, client):
        if not bucket:
            raise ValueError("S3 bucket is required")
        self.bucket = bucket
        self.client = client

    @staticmethod
    def _key(digest: str) -> str:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Invalid SHA256")
        return f"sha256/{digest[:2]}/{digest}"

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(digest), Body=data,
            Metadata={"sha256": digest},
        )
        return digest

    def get(self, digest: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(digest))
        body = response["Body"]
        data = body.read() if hasattr(body, "read") else bytes(body)
        if hashlib.sha256(data).hexdigest() != digest:
            raise IOError("Object checksum mismatch")
        return data
