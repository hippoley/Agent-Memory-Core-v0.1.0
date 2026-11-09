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
