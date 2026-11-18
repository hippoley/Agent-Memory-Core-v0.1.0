-- Identity foundation (control-plane data; never grant memory_tenant access).
-- OIDC verification and request authorization are implemented in later slices.
-- This migration stores identifiers and lifecycle state, not bearer/session secrets.
BEGIN;

CREATE TABLE IF NOT EXISTS memory_principals (
  principal_id uuid PRIMARY KEY,
  principal_type text NOT NULL CHECK (principal_type IN ('human','service')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','deleted')),
  display_name text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS memory_external_identities (
  identity_id uuid PRIMARY KEY,
  principal_id uuid NOT NULL REFERENCES memory_principals(principal_id) ON DELETE CASCADE,
  issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
  subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 1024),
  email text,
  email_verified boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_authenticated_at timestamptz,
  UNIQUE (issuer, subject),
  UNIQUE (principal_id, identity_id)
);
CREATE INDEX IF NOT EXISTS memory_external_identities_principal
  ON memory_external_identities(principal_id);

CREATE TABLE IF NOT EXISTS memory_tenants (
  tenant_id text PRIMARY KEY CHECK (length(tenant_id) BETWEEN 1 AND 255),
  display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 255),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','deleted')),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS memory_tenant_memberships (
  membership_id uuid PRIMARY KEY,
  tenant_id text NOT NULL REFERENCES memory_tenants(tenant_id) ON DELETE CASCADE,
  principal_id uuid NOT NULL REFERENCES memory_principals(principal_id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('owner','admin','editor','reader')),
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','suspended')),
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  revoked_at timestamptz,
  UNIQUE (tenant_id, principal_id),
  UNIQUE (tenant_id, membership_id),
  CHECK ((status = 'revoked') = (revoked_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS memory_memberships_principal_active
  ON memory_tenant_memberships(principal_id, status, tenant_id);

CREATE TABLE IF NOT EXISTS memory_sessions (
  session_id uuid PRIMARY KEY,
  principal_id uuid NOT NULL REFERENCES memory_principals(principal_id) ON DELETE CASCADE,
  token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
  issued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_seen_at timestamptz,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  session_version bigint NOT NULL DEFAULT 1 CHECK (session_version > 0),
  auth_time timestamptz NOT NULL DEFAULT clock_timestamp(),
  user_agent_hash bytea CHECK (user_agent_hash IS NULL OR octet_length(user_agent_hash) = 32),
  CHECK (expires_at > issued_at)
);
CREATE INDEX IF NOT EXISTS memory_sessions_principal_expiry
  ON memory_sessions(principal_id, expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS memory_api_credentials (
  credential_id uuid PRIMARY KEY,
  principal_id uuid NOT NULL REFERENCES memory_principals(principal_id) ON DELETE CASCADE,
  tenant_id text NOT NULL REFERENCES memory_tenants(tenant_id) ON DELETE CASCADE,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
  secret_hash bytea NOT NULL UNIQUE CHECK (octet_length(secret_hash) = 32),
  secret_prefix text NOT NULL CHECK (length(secret_prefix) BETWEEN 4 AND 24),
  scopes text[] NOT NULL CHECK (cardinality(scopes) > 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  expires_at timestamptz,
  last_used_at timestamptz,
  revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS memory_api_credentials_owner
  ON memory_api_credentials(principal_id, tenant_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS memory_identity_audit_events (
  audit_id uuid PRIMARY KEY,
  occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  correlation_id text NOT NULL CHECK (length(correlation_id) BETWEEN 1 AND 255),
  principal_id uuid REFERENCES memory_principals(principal_id) ON DELETE SET NULL,
  tenant_id text REFERENCES memory_tenants(tenant_id) ON DELETE SET NULL,
  action text NOT NULL CHECK (length(action) BETWEEN 1 AND 120),
  resource_type text,
  resource_id text,
  decision text NOT NULL CHECK (decision IN ('allow','deny','system')),
  reason_code text NOT NULL CHECK (length(reason_code) BETWEEN 1 AND 120),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS memory_identity_audit_tenant_time
  ON memory_identity_audit_events(tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS memory_identity_audit_principal_time
  ON memory_identity_audit_events(principal_id, occurred_at DESC);

-- Identity/control-plane records are intentionally unavailable to tenant SQL roles.
REVOKE ALL ON memory_principals, memory_external_identities, memory_tenants,
  memory_tenant_memberships, memory_sessions, memory_api_credentials,
  memory_identity_audit_events FROM PUBLIC;
COMMIT;
