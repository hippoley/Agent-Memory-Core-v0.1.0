-- Evidence producer trust registry and replay protection.
-- Public keys only: private signing material remains with the producer.
BEGIN;

CREATE TABLE IF NOT EXISTS memory_evidence_producer_keys (
  issuer text NOT NULL CHECK (length(issuer) BETWEEN 1 AND 2048),
  key_id text NOT NULL CHECK (length(key_id) BETWEEN 1 AND 255),
  principal_id uuid REFERENCES memory_principals(principal_id) ON DELETE SET NULL,
  public_key bytea NOT NULL CHECK (octet_length(public_key) = 32),
  allowed_sources text[] NOT NULL CHECK (cardinality(allowed_sources) > 0),
  allowed_tenants text[] NOT NULL CHECK (cardinality(allowed_tenants) > 0),
  audience text NOT NULL CHECK (length(audience) BETWEEN 1 AND 255),
  not_before timestamptz NOT NULL,
  expires_at timestamptz,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (issuer, key_id),
  CHECK (expires_at IS NULL OR expires_at > not_before)
);

CREATE INDEX IF NOT EXISTS memory_evidence_producer_keys_active
  ON memory_evidence_producer_keys(issuer, key_id)
  WHERE revoked_at IS NULL;

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
  PRIMARY KEY (issuer, key_id, nonce),
  FOREIGN KEY (issuer, key_id)
    REFERENCES memory_evidence_producer_keys(issuer, key_id)
    ON DELETE RESTRICT,
  CHECK (expires_at > issued_at)
);

CREATE INDEX IF NOT EXISTS memory_evidence_nonces_expiry
  ON memory_evidence_nonces(expires_at);

REVOKE ALL ON memory_evidence_producer_keys, memory_evidence_nonces FROM PUBLIC;
COMMIT;
