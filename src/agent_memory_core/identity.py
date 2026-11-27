"""Control-plane identity store for principals, memberships and opaque sessions.

Use a dedicated control-plane PostgreSQL login. Never pass a tenant data-plane
connection or a browser-supplied tenant id without membership verification.
Raw session/API secrets are returned once and are never persisted.
"""
from __future__ import annotations

import hashlib
import secrets
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.parse import urlparse


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

    def complete_oidc_login(self, *, issuer: str, subject: str,
                            email: str | None = None,
                            email_verified: bool = False,
                            display_name: str | None = None,
                            allow_signup: bool = False,
                            bootstrap_workspace: bool = True,
                            user_agent: str | None = None) -> dict:
        """Atomically bind a verified OIDC subject, bootstrap its first workspace,
        and issue a browser session.

        Call only after the OIDC ID token has been fully verified (signature,
        issuer, audience, expiry and nonce). The issuer/subject pair is the
        identity key; email is profile metadata and is never used for account
        linking.
        """
        issuer_host = urlparse(issuer).hostname if isinstance(issuer, str) else None
        local_http_issuer = (
            isinstance(issuer, str) and issuer.startswith("http://")
            and os.getenv("MEMORY_OIDC_ALLOW_INSECURE_HTTP", "0") == "1"
            and issuer_host is not None
            and (issuer_host in {"localhost", "127.0.0.1", "::1"}
                 or issuer_host.endswith(".localhost"))
        )
        if not isinstance(issuer, str) or (not issuer.startswith("https://") and not local_http_issuer):
            raise ValueError("OIDC issuer must use HTTPS except explicit localhost development")
        if not isinstance(subject, str) or not subject or len(subject) > 1024:
            raise ValueError("OIDC subject is required")
        if email is not None and len(email) > 320:
            email = None
        label = (display_name or (email.split("@", 1)[0] if email and "@" in email else None)
                 or "My Workspace").strip()[:255] or "My Workspace"
        token = secrets.token_urlsafe(32)
        session_id = uuid4()
        user_agent_hash = self._hash(user_agent) if user_agent else None
        is_new_principal = False
        created_workspace = False

        with self.conn.transaction():
            # Serialize first-login races for the same issuer/subject pair.
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (issuer + "\\n" + subject,))
            linked = self.conn.execute(
                """SELECT p.principal_id::text,p.status
                   FROM memory_external_identities e
                   JOIN memory_principals p ON p.principal_id=e.principal_id
                   WHERE e.issuer=%s AND e.subject=%s""",
                (issuer, subject)).fetchone()

            if linked is None:
                if not allow_signup:
                    raise LookupError("No account is linked to this OIDC identity")
                principal_id = uuid4()
                self.conn.execute(
                    """INSERT INTO memory_principals
                       (principal_id,principal_type,display_name)
                       VALUES (%s,'human',%s)""", (principal_id, label))
                self.conn.execute(
                    """INSERT INTO memory_external_identities
                       (identity_id,principal_id,issuer,subject,email,email_verified)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (uuid4(), principal_id, issuer, subject, email, bool(email_verified)))
                is_new_principal = True
            else:
                principal_id, principal_status = linked
                if principal_status != "active":
                    raise PermissionError("Principal is not active")
                self.conn.execute(
                    """UPDATE memory_external_identities
                       SET email=%s,email_verified=%s,last_authenticated_at=clock_timestamp()
                       WHERE issuer=%s AND subject=%s""",
                    (email, bool(email_verified), issuer, subject))

            active_memberships = self.conn.execute(
                """SELECT m.tenant_id,t.display_name,m.role
                   FROM memory_tenant_memberships m
                   JOIN memory_tenants t ON t.tenant_id=m.tenant_id
                   WHERE m.principal_id=%s AND m.status='active' AND t.status='active'
                   ORDER BY m.created_at,m.tenant_id""",
                (principal_id,)).fetchall()

            if not active_memberships and bootstrap_workspace and is_new_principal:
                tenant_id = "ws-" + uuid4().hex
                self.conn.execute(
                    "INSERT INTO memory_tenants(tenant_id,display_name) VALUES (%s,%s)",
                    (tenant_id, label))
                self.conn.execute(
                    """INSERT INTO memory_tenant_memberships
                       (membership_id,tenant_id,principal_id,role)
                       VALUES (%s,%s,%s,'owner')""",
                    (uuid4(), tenant_id, principal_id))
                active_memberships = [(tenant_id, label, "owner")]
                created_workspace = True

            if not active_memberships:
                raise LookupError("OIDC principal has no active tenant membership")

            expires = self.conn.execute(
                """INSERT INTO memory_sessions
                   (session_id,principal_id,token_hash,expires_at,user_agent_hash)
                   VALUES (%s,%s,%s,clock_timestamp()+interval '12 hours',%s)
                   RETURNING expires_at""",
                (session_id, principal_id, self._hash(token), user_agent_hash)
            ).fetchone()[0]
            self.conn.execute(
                """INSERT INTO memory_identity_audit_events
                   (audit_id,correlation_id,principal_id,action,decision,reason_code,metadata)
                   VALUES (%s,%s,%s,'identity.oidc.login','allow','oidc_verified',%s::jsonb)""",
                (uuid4(), str(session_id), principal_id,
                 '{"provider":"oidc","new_principal":' + ("true" if is_new_principal else "false") + '}'))

        return {
            "principal_id": str(principal_id),
            "session_id": str(session_id),
            "token": token,
            "expires_at": expires,
            "is_new_principal": is_new_principal,
            "created_workspace": created_workspace,
            "tenants": [
                {"tenant_id": row[0], "display_name": row[1], "role": row[2]}
                for row in active_memberships
            ],
        }

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
        with self.conn.transaction():
            # Serialize owner transfers/downgrades per tenant so concurrent
            # administrators cannot remove the final active owner.
            self.conn.execute(
                "SELECT tenant_id FROM memory_tenants WHERE tenant_id=%s FOR UPDATE",
                (tenant_id,))
            current = self.conn.execute(
                """SELECT role FROM memory_tenant_memberships
                   WHERE tenant_id=%s AND principal_id=%s AND status='active'
                   FOR UPDATE""", (tenant_id, principal_id)).fetchone()
            if current is None:
                raise LookupError("Active membership not found")
            if current[0] == "owner" and role != "owner":
                owners = self.conn.execute(
                    """SELECT count(*) FROM memory_tenant_memberships
                       WHERE tenant_id=%s AND role='owner' AND status='active'""",
                    (tenant_id,)).fetchone()[0]
                if owners <= 1:
                    raise ValueError("Cannot demote the last active tenant owner")
            row = self.conn.execute(
                """UPDATE memory_tenant_memberships
                   SET role=%s,version=version+1,updated_at=clock_timestamp()
                   WHERE tenant_id=%s AND principal_id=%s AND status='active'
                   RETURNING version""", (role, tenant_id, principal_id)).fetchone()
            return int(row[0])

    def revoke_membership(self, *, tenant_id: str, principal_id: str) -> bool:
        with self.conn.transaction():
            self.conn.execute(
                "SELECT tenant_id FROM memory_tenants WHERE tenant_id=%s FOR UPDATE",
                (tenant_id,))
            current = self.conn.execute(
                """SELECT role FROM memory_tenant_memberships
                   WHERE tenant_id=%s AND principal_id=%s AND status='active'
                   FOR UPDATE""", (tenant_id, principal_id)).fetchone()
            if current is None:
                return False
            if current[0] == "owner":
                owners = self.conn.execute(
                    """SELECT count(*) FROM memory_tenant_memberships
                       WHERE tenant_id=%s AND role='owner' AND status='active'""",
                    (tenant_id,)).fetchone()[0]
                if owners <= 1:
                    raise ValueError("Cannot revoke the last active tenant owner")
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

    def authorize_background_execution(self, *, principal_id: str,
                                      tenant_id: str, session_id: str | None = None,
                                      credential_id: str | None = None,
                                      membership_version: int | None = None) -> bool:
        """Authorize queued work against current control-plane state.

        This check runs immediately before a durable worker performs a side
        effect. Revoked sessions, memberships, suspended principals/tenants,
        and membership version changes therefore invalidate queued work.
        """
        if not principal_id or not tenant_id:
            return False
        sql = """SELECT 1
                 FROM memory_principals p
                 JOIN memory_tenant_memberships m
                   ON m.principal_id=p.principal_id
                  AND m.tenant_id=%s
                  AND m.status='active'
                 JOIN memory_tenants t
                   ON t.tenant_id=m.tenant_id AND t.status='active'
                 WHERE p.principal_id=%s AND p.status='active'
                   AND m.role IN ('owner','admin','editor')"""
        params=[tenant_id, principal_id]
        if membership_version is not None:
            sql += " AND m.version=%s"
            params.append(int(membership_version))
        if session_id is not None:
            sql += """ AND EXISTS (
                         SELECT 1 FROM memory_sessions s
                         WHERE s.session_id=%s::uuid
                           AND s.principal_id=p.principal_id
                           AND s.revoked_at IS NULL
                           AND s.expires_at>clock_timestamp()
                       )"""
            params.append(session_id)
        if credential_id is not None:
            sql += """ AND EXISTS (
                         SELECT 1 FROM memory_api_credentials c
                         WHERE c.credential_id=%s::uuid
                           AND c.principal_id=p.principal_id
                           AND c.tenant_id=m.tenant_id
                           AND c.revoked_at IS NULL
                           AND (c.expires_at IS NULL OR c.expires_at>clock_timestamp())
                           AND ('memory:write'=ANY(c.scopes) OR 'memory:*'=ANY(c.scopes))
                       )"""
            params.append(credential_id)
        return self.conn.execute(sql, params).fetchone() is not None

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
