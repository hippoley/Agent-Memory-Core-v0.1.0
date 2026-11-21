-- AMC knowledge source version authority, reusing existing evidence producer
-- keys and service principals. This is NOT a second key registry.
BEGIN;
CREATE TABLE IF NOT EXISTS memory_knowledge_source_revisions (
  tenant_id text NOT NULL REFERENCES memory_tenants(tenant_id) ON DELETE RESTRICT,
  subject_id text NOT NULL CHECK (length(subject_id) BETWEEN 1 AND 255),
  predicate text NOT NULL CHECK (length(predicate) BETWEEN 1 AND 255),
  source_id text NOT NULL CHECK (length(source_id) BETWEEN 1 AND 2048),
  issuer text NOT NULL,
  key_id text NOT NULL,
  revoked_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, subject_id, predicate),
  FOREIGN KEY (issuer, key_id)
    REFERENCES memory_evidence_producer_keys(issuer, key_id) ON DELETE RESTRICT
);
-- A control-plane administrator, not a model/tenant SQL login, registers
-- active revisions. There is no user-facing route to update this table.
REVOKE ALL ON memory_knowledge_source_revisions FROM PUBLIC;
COMMIT;
