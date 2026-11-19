-- Evidence trust registry, four-dimensional authority grants and replay/fact ledgers.
-- Public keys only: private signing material remains with the producer.
BEGIN;

CREATE TABLE IF NOT EXISTS memory_evidence_producer_keys (
  issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
  key_id text NOT NULL CHECK (length(key_id) BETWEEN 1 AND 255),
  principal_id uuid NOT NULL REFERENCES memory_principals(principal_id) ON DELETE RESTRICT,
  public_key bytea NOT NULL CHECK (octet_length(public_key) = 32),
  audience text NOT NULL CHECK (length(audience) BETWEEN 1 AND 255),
  not_before timestamptz NOT NULL,
  expires_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (issuer, key_id),
  CHECK (expires_at IS NULL OR expires_at > not_before)
);

-- Safe when an earlier unmerged draft of migration 010 created the coarse
-- source/tenant allowlists. The normalized grants below supersede those columns.
ALTER TABLE memory_evidence_producer_keys
  DROP COLUMN IF EXISTS allowed_sources,
  DROP COLUMN IF EXISTS allowed_tenants,
  ALTER COLUMN principal_id SET NOT NULL;
ALTER TABLE memory_evidence_producer_keys
  DROP CONSTRAINT IF EXISTS memory_evidence_producer_keys_principal_id_fkey,
  DROP CONSTRAINT IF EXISTS memory_evidence_producer_keys_principal_fk;
ALTER TABLE memory_evidence_producer_keys
  ADD CONSTRAINT memory_evidence_producer_keys_principal_fk
  FOREIGN KEY (principal_id) REFERENCES memory_principals(principal_id)
  ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS memory_evidence_authority_grants (
  issuer text NOT NULL,
  key_id text NOT NULL,
  evidence_source text NOT NULL CHECK (evidence_source IN (
    'postgres_task','postgres_dispatch','postgres_worker_lease',
    'temporal_history','temporal_lookup','activity_heartbeat',
    'effect_ledger','provider_receipt'
  )),
  claim_type text NOT NULL CHECK (length(claim_type) BETWEEN 1 AND 160),
  tenant_scope text NOT NULL CHECK (length(tenant_scope) BETWEEN 1 AND 255),
  task_type_scope text NOT NULL DEFAULT '*'
    CHECK (length(task_type_scope) BETWEEN 1 AND 160),
  allow_authoritative boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (
    issuer,key_id,evidence_source,claim_type,tenant_scope,task_type_scope
  ),
  FOREIGN KEY (issuer,key_id)
    REFERENCES memory_evidence_producer_keys(issuer,key_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS memory_evidence_authority_lookup
  ON memory_evidence_authority_grants(
    issuer,key_id,evidence_source,claim_type,tenant_scope,task_type_scope
  );

CREATE TABLE IF NOT EXISTS memory_evidence_nonces (
  issuer text NOT NULL,
  key_id text NOT NULL,
  nonce text NOT NULL CHECK (length(nonce) BETWEEN 16 AND 255),
  evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  tenant_id text NOT NULL,
  task_id text NOT NULL,
  issued_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  consumed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (issuer,key_id,nonce),
  FOREIGN KEY (issuer,key_id)
    REFERENCES memory_evidence_producer_keys(issuer,key_id) ON DELETE RESTRICT,
  CHECK (expires_at > issued_at)
);

-- Semantic identity is independent of attestation nonce and envelope timestamp.
-- A changed payload for the same immutable fact identity is a conflict, not a new fact.
CREATE TABLE IF NOT EXISTS memory_evidence_facts (
  tenant_id text NOT NULL,
  task_id text NOT NULL,
  evidence_source text NOT NULL,
  claim_type text NOT NULL,
  workflow_id text NOT NULL DEFAULT '',
  run_id text NOT NULL DEFAULT '',
  lease_epoch bigint NOT NULL DEFAULT -1 CHECK (lease_epoch >= -1),
  fact_id text NOT NULL DEFAULT '',
  semantic_sha256 text NOT NULL CHECK (semantic_sha256 ~ '^[0-9a-f]{64}$'),
  first_evidence_id text NOT NULL,
  first_issuer text NOT NULL,
  first_key_id text NOT NULL,
  first_observed_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (
    tenant_id,task_id,evidence_source,claim_type,
    workflow_id,run_id,lease_epoch,fact_id
  )
);

CREATE INDEX IF NOT EXISTS memory_evidence_nonces_expiry
  ON memory_evidence_nonces(expires_at);
CREATE INDEX IF NOT EXISTS memory_evidence_facts_task
  ON memory_evidence_facts(tenant_id,task_id,created_at DESC);

REVOKE ALL ON memory_evidence_producer_keys,
  memory_evidence_authority_grants,memory_evidence_nonces,
  memory_evidence_facts FROM PUBLIC;
COMMIT;
