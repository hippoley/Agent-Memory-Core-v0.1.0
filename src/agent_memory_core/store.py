"""Bitemporal, provenance-aware SQLite reference backend.

Time semantics:
- valid_from/valid_to: asserted real-world validity interval [from,to)
- recorded_at: when this assertion entered the system, NOT necessarily when true
- superseded_at: when system stopped treating assertion as current
Both recorded and superseded times are application-supplied UTC timestamps.

All reads are scoped to tenant_id. This is not an authorization engine: callers
must authenticate/authorize before calling; tenant scoping is defense-in-depth.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Optional


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _require_aware(ts: str) -> str:
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("Timestamp must carry timezone offset")
    return d.astimezone(timezone.utc).isoformat(timespec="microseconds")


class MemoryConflict(Exception):
    pass


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    tenant_id: str
    subject_id: str
    predicate: str
    value: str
    source_id: str
    valid_from: str
    valid_to: Optional[str]
    recorded_at: str
    superseded_at: Optional[str]
    replaces_id: Optional[str]
    object_sha256: Optional[str]


class MemoryStore:
    def __init__(self, db_path: str | Path):
        self.db = sqlite3.connect(str(db_path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          subject_id TEXT NOT NULL,
          predicate TEXT NOT NULL,
          value TEXT NOT NULL,
          source_id TEXT NOT NULL,
          valid_from TEXT NOT NULL,
          valid_to TEXT,
          recorded_at TEXT NOT NULL,
          superseded_at TEXT,
          replaces_id TEXT,
          object_sha256 TEXT,
          CHECK(valid_to IS NULL OR valid_to > valid_from),
          FOREIGN KEY(replaces_id) REFERENCES memories(id)
        );
        CREATE INDEX IF NOT EXISTS idx_memories_lookup
        ON memories(tenant_id, subject_id, predicate, recorded_at);
        CREATE TABLE IF NOT EXISTS events (
          event_id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          event_type TEXT NOT NULL,
          payload TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_tenant_time
        ON events(tenant_id, recorded_at);
        """)

    def close(self):
        self.db.close()

    def remember(self, *, tenant_id: str, subject_id: str, predicate: str,
                 value: str, source_id: str, valid_from: str,
                 valid_to: Optional[str] = None, replaces_id: Optional[str] = None,
                 object_sha256: Optional[str] = None, recorded_at: Optional[str] = None) -> str:
        if not all([tenant_id, subject_id, predicate, value, source_id]):
            raise ValueError("tenant, subject, predicate, value and source required")
        valid_from = _require_aware(valid_from)
        valid_to = _require_aware(valid_to) if valid_to else None
        recorded_at = _require_aware(recorded_at) if recorded_at else utcnow()
        if valid_to and valid_to <= valid_from:
            raise ValueError("valid_to must exceed valid_from")
        mid = str(uuid.uuid4())
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if replaces_id:
                old = self.db.execute(
                    "SELECT tenant_id, subject_id, predicate, recorded_at, superseded_at FROM memories WHERE id=?",
                    (replaces_id,)).fetchone()
                if not old or (old["tenant_id"], old["subject_id"], old["predicate"]) != (tenant_id, subject_id, predicate):
                    raise MemoryConflict("Replacement must refer to same tenant/subject/predicate")
                if old["superseded_at"] is not None or recorded_at < old["recorded_at"]:
                    raise MemoryConflict("Already superseded or invalid recording order")
                self.db.execute("UPDATE memories SET superseded_at=? WHERE id=?", (recorded_at, replaces_id))
            self.db.execute("""INSERT INTO memories
                (id,tenant_id,subject_id,predicate,value,source_id,valid_from,valid_to,
                 recorded_at,superseded_at,replaces_id,object_sha256)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mid,tenant_id,subject_id,predicate,value,source_id,valid_from,valid_to,
                 recorded_at,None,replaces_id,object_sha256))
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return mid

    def recall(self, *, tenant_id: str, subject_id: str, predicate: Optional[str] = None,
               valid_at: Optional[str] = None, known_at: Optional[str] = None) -> list[MemoryRecord]:
        """What was believed at known_at about the world at valid_at?
        When known_at omitted, use now. No unscoped cross-tenant API.
        """
        known_at = _require_aware(known_at) if known_at else utcnow()
        valid_at = _require_aware(valid_at) if valid_at else known_at
        sql = """SELECT * FROM memories WHERE tenant_id=? AND subject_id=?
                 AND recorded_at<=? AND (superseded_at IS NULL OR superseded_at>?)
                 AND valid_from<=? AND (valid_to IS NULL OR valid_to>?)"""
        args = [tenant_id, subject_id, known_at, known_at, valid_at, valid_at]
        if predicate is not None:
            sql += " AND predicate=?"
            args.append(predicate)
        sql += " ORDER BY recorded_at DESC, id"
        return [MemoryRecord(**dict(r)) for r in self.db.execute(sql, args)]

    def history(self, *, tenant_id: str, subject_id: str) -> list[MemoryRecord]:
        return [MemoryRecord(**dict(r)) for r in self.db.execute(
            "SELECT * FROM memories WHERE tenant_id=? AND subject_id=? ORDER BY recorded_at,id",
            (tenant_id, subject_id))]

    def append_event(self, *, tenant_id: str, event_id: str, event_type: str,
                     payload: dict, recorded_at: Optional[str] = None) -> bool:
        """Idempotent event insertion. Duplicate IDs with changed contents are rejected."""
        if not tenant_id or not event_id or not event_type:
            raise ValueError("tenant, event ID and event type required")
        stamp = _require_aware(recorded_at) if recorded_at else utcnow()
        packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT tenant_id,event_type,payload FROM events WHERE event_id=?", (event_id,)).fetchone()
            if old:
                if (old["tenant_id"], old["event_type"], old["payload"]) != (tenant_id,event_type,packed):
                    raise MemoryConflict("Event ID collision with different content")
                self.db.execute("COMMIT")
                return False
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (event_id, tenant_id,event_type,packed,stamp))
            self.db.execute("COMMIT")
            return True
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def events(self, *, tenant_id: str) -> list[dict]:
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in self.db.execute(
            "SELECT * FROM events WHERE tenant_id=? ORDER BY recorded_at,event_id", (tenant_id,))]
