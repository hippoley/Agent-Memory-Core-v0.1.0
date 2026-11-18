"""Control-plane identity store for principals, memberships and opaque sessions.

Use a dedicated control-plane PostgreSQL login. Never pass a tenant data-plane
connection or a browser-supplied tenant id without membership verification.
Raw session/API secrets are returned once and are never persisted.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4


class IdentityStore:
    def __init__(self, dsn: str):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Install the 'postgres' extra: pip install -e '.[postgres]'") from exc
        self.conn = psycopg.connect(dsn, autocommit=True)

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _hash(secret: str) -> bytes:
        return hashlib.sha256(secret.encode("utf-8")).digest()

    def create_principal(self, *, principal_type: str = "human",
                         display_name: str | None = None) -> str:
        if principal_type not in ("human", "service"):
            raise ValueError("principal_type must be human or service")
        principal_id = uuid4()
        self.conn.execute(
            """INSERT INTO memory_principals(principal_id,principal_type,display_name)
               VALUES (%s,%s,%s)""", (principal_id, principal_type, display_name))
        return str(principal_id)

    def link_external_identity(self, *, principal_id: str, issuer: str,
                               subject: str, email: str | None = None,
                               email_verified: bool = False) -> str:
        identity_id = uuid4()
        self.conn.execute(
            """INSERT INTO memory_external_identities
               (identity_id,principal_id,issuer,subject,email,email_verified)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (identity_id, principal_id, issuer, subject, email, email_verified))
        return str(identity_id)

    def create_tenant(self, *, tenant_id: str, display_name: str,
                      owner_principal_id: str) -> str:
        if not tenant_id or not display_name:
            raise ValueError("tenant_id and display_name are required")
        membership_id = uuid4()
        with self.conn.transaction():
            self.conn.execute(
                "INSERT INTO memory_tenants(tenant_id,display_name) VALUES (%s,%s)",
                (tenant_id, display_name))
            self.conn.execute(
                """INSERT INTO memory_tenant_memberships
                   (membership_id,tenant_id,principal_id,role)
                   VALUES (%s,%s,%s,'owner')""",
                (membership_id, tenant_id, owner_principal_id))
        return tenant_id

    def add_membership(self, *, tenant_id: str, principal_id: str,
                       role: str = "reader") -> str:
        if role not in ("owner", "admin", "editor", "reader"):
            raise ValueError("Invalid membership role")
        membership_id = uuid4()
        self.conn.execute(
            """INSERT INTO memory_tenant_memberships
               (membership_id,tenant_id,principal_id,role)
               VALUES (%s,%s,%s,%s)""",
            (membership_id, tenant_id, principal_id, role))
        return str(membership_id)

    def set_membership_role(self, *, tenant_id: str, principal_id: str,
                            role: str) -> int:
        if role not in ("owner", "admin", "editor", "reader"):
            raise ValueError("Invalid membership role")
        row = self.conn.execute(
            """UPDATE memory_tenant_memberships
               SET role=%s,version=version+1,updated_at=clock_timestamp()
               WHERE tenant_id=%s AND principal_id=%s AND status='active'
               RETURNING version""", (role, tenant_id, principal_id)).fetchone()
        if row is None:
            raise LookupError("Active membership not found")
        return int(row[0])

    def revoke_membership(self, *, tenant_id: str, principal_id: str) -> bool:
        row = self.conn.execute(
            """UPDATE memory_tenant_memberships
               SET status='revoked',revoked_at=clock_timestamp(),
                   version=version+1,updated_at=clock_timestamp()
               WHERE tenant_id=%s AND principal_id=%s AND status='active'
               RETURNING membership_id""", (tenant_id, principal_id)).fetchone()
        return row is not None

    def issue_session(self, *, principal_id: str, ttl: timedelta = timedelta(hours=12),
                      user_agent: str | None = None) -> dict:
        if ttl <= timedelta(0) or ttl > timedelta(days=30):
            raise ValueError("Session TTL must be between 0 and 30 days")
        token = secrets.token_urlsafe(32)
        session_id = uuid4()
        user_agent_hash = self._hash(user_agent) if user_agent else None
        row = self.conn.execute(
            """INSERT INTO memory_sessions
               (session_id,principal_id,token_hash,expires_at,user_agent_hash)
               SELECT %s,p.principal_id,%s,clock_timestamp()+(%s * interval '1 second'),%s
               FROM memory_principals p WHERE p.principal_id=%s AND p.status='active'
               RETURNING expires_at""",
            (session_id, self._hash(token), int(ttl.total_seconds()),
             user_agent_hash, principal_id)).fetchone()
        if row is None:
            raise LookupError("Active principal not found")
        return {"session_id": str(session_id), "token": token,
                "expires_at": row[0]}

    def list_session_tenants(self, *, token: str) -> list[dict]:
        if not token:
            return []
        rows = self.conn.execute(
            """SELECT m.tenant_id,t.display_name,m.role,m.version
               FROM memory_sessions s
               JOIN memory_principals p ON p.principal_id=s.principal_id
               JOIN memory_tenant_memberships m ON m.principal_id=p.principal_id
               JOIN memory_tenants t ON t.tenant_id=m.tenant_id
               WHERE s.token_hash=%s AND s.revoked_at IS NULL
                 AND s.expires_at>clock_timestamp()
                 AND p.status='active' AND m.status='active' AND t.status='active'
               ORDER BY t.display_name,m.tenant_id""",
            (self._hash(token),)).fetchall()
        return [{"tenant_id": r[0], "display_name": r[1],
                 "role": r[2], "membership_version": int(r[3])}
                for r in rows]

    def authenticate_session(self, *, token: str, tenant_id: str) -> dict | None:
        if not token or not tenant_id:
            return None
        row = self.conn.execute(
            """SELECT s.session_id::text,s.principal_id::text,p.principal_type,
                      m.tenant_id,m.role,m.version,t.status
               FROM memory_sessions s
               JOIN memory_principals p ON p.principal_id=s.principal_id
               JOIN memory_tenant_memberships m ON m.principal_id=p.principal_id
               JOIN memory_tenants t ON t.tenant_id=m.tenant_id
               WHERE s.token_hash=%s AND s.revoked_at IS NULL
                 AND s.expires_at>clock_timestamp()
                 AND p.status='active' AND m.status='active'
                 AND t.status='active' AND m.tenant_id=%s""",
            (self._hash(token), tenant_id)).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE memory_sessions SET last_seen_at=clock_timestamp() WHERE session_id=%s",
            (row[0],))
        return {"session_id": row[0], "principal_id": row[1],
                "principal_type": row[2], "tenant_id": row[3],
                "role": row[4], "membership_version": int(row[5])}

    def revoke_session(self, *, session_id: str, principal_id: str) -> bool:
        row = self.conn.execute(
            """UPDATE memory_sessions SET revoked_at=clock_timestamp()
               WHERE session_id=%s AND principal_id=%s AND revoked_at IS NULL
               RETURNING session_id""", (session_id, principal_id)).fetchone()
        return row is not None

    def issue_api_credential(self, *, principal_id: str, tenant_id: str,
                             name: str, scopes: list[str],
                             ttl: timedelta | None = None) -> dict:
        if not scopes or any(not isinstance(x, str) or not x for x in scopes):
            raise ValueError("At least one non-empty scope is required")
        if ttl is not None and (ttl <= timedelta(0) or ttl > timedelta(days=365)):
            raise ValueError("Credential TTL must be between 0 and 365 days")
        secret = "amc_" + secrets.token_urlsafe(32)
        credential_id = uuid4()
        expires_at = datetime.now(timezone.utc) + ttl if ttl else None
        row = self.conn.execute(
            """INSERT INTO memory_api_credentials
               (credential_id,principal_id,tenant_id,name,secret_hash,
                secret_prefix,scopes,expires_at)
               SELECT %s,m.principal_id,m.tenant_id,%s,%s,%s,%s,%s
               FROM memory_tenant_memberships m
               JOIN memory_principals p ON p.principal_id=m.principal_id
               WHERE m.tenant_id=%s AND m.principal_id=%s
                 AND m.status='active' AND p.status='active'
               RETURNING credential_id""",
            (credential_id,name,self._hash(secret),secret[:12],scopes,
             expires_at,tenant_id,principal_id)
        ).fetchone()
        if row is None:
            raise LookupError("Active tenant membership not found")
        return {"credential_id": str(row[0]), "secret": secret}

    def authenticate_api_credential(self, *, secret: str) -> dict | None:
        row = self.conn.execute(
            """SELECT c.credential_id::text,c.principal_id::text,c.tenant_id,
                      c.scopes,m.role,m.version
               FROM memory_api_credentials c
               JOIN memory_principals p ON p.principal_id=c.principal_id
               JOIN memory_tenants t ON t.tenant_id=c.tenant_id
               JOIN memory_tenant_memberships m
                 ON m.principal_id=c.principal_id AND m.tenant_id=c.tenant_id
               WHERE c.secret_hash=%s AND c.revoked_at IS NULL
                 AND (c.expires_at IS NULL OR c.expires_at>clock_timestamp())
                 AND p.status='active' AND t.status='active' AND m.status='active'""",
            (self._hash(secret),)).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE memory_api_credentials SET last_used_at=clock_timestamp() WHERE credential_id=%s",
            (row[0],))
        return {"credential_id": row[0], "principal_id": row[1],
                "tenant_id": row[2], "scopes": list(row[3]),
                "role": row[4], "membership_version": int(row[5])}

    def revoke_api_credential(self, *, credential_id: str, principal_id: str) -> bool:
        row = self.conn.execute(
            """UPDATE memory_api_credentials SET revoked_at=clock_timestamp()
               WHERE credential_id=%s AND principal_id=%s AND revoked_at IS NULL
               RETURNING credential_id""", (credential_id, principal_id)).fetchone()
        return row is not None
