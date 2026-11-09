-- Agent-Memory-Core PostgreSQL v0.2 prototype.
-- Requires pgvector; AGE is intentionally initialized separately.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_claims (
  id uuid PRIMARY KEY,
  tenant_id text NOT NULL,
  subject_id text NOT NULL,
  predicate text NOT NULL,
  value jsonb NOT NULL,
  source_id text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_to timestamptz,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  superseded_at timestamptz,
  replaces_id uuid REFERENCES memory_claims(id),
  object_sha256 text,
  CHECK (valid_to IS NULL OR valid_to > valid_from),
  CHECK (superseded_at IS NULL OR superseded_at >= recorded_at),
  CHECK (object_sha256 IS NULL OR object_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS memory_claims_asof
ON memory_claims(tenant_id, subject_id, predicate, recorded_at DESC);
CREATE INDEX IF NOT EXISTS memory_claims_valid
ON memory_claims(tenant_id, valid_from, valid_to);
CREATE TABLE IF NOT EXISTS memory_events (
  tenant_id text NOT NULL,
  event_id text NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id,event_id)
);
CREATE TABLE IF NOT EXISTS memory_embedding_models (
  tenant_id text NOT NULL,
  model_id text NOT NULL,
  dimensions integer NOT NULL CHECK (dimensions IN (384,512)),
  PRIMARY KEY (tenant_id,model_id)
);
CREATE TABLE IF NOT EXISTS memory_embeddings_512 (
  memory_id uuid NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
  model_id text NOT NULL,
  embedding vector(512) NOT NULL,
  PRIMARY KEY(memory_id,model_id)
);
CREATE INDEX IF NOT EXISTS memory_embeddings_512_hnsw
  ON memory_embeddings_512 USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS memory_embeddings (
  memory_id uuid NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
  model_id text NOT NULL,
  embedding vector(384) NOT NULL,
  PRIMARY KEY(memory_id,model_id)
);
CREATE INDEX IF NOT EXISTS memory_embeddings_hnsw
ON memory_embeddings USING hnsw (embedding vector_cosine_ops);
-- Upgrade path for databases that had only 384-dimensional vectors.
-- Preserve existing rows before the model registry starts governing queries.
INSERT INTO memory_embedding_models(tenant_id,model_id,dimensions)
SELECT DISTINCT c.tenant_id, e.model_id, 384
FROM memory_embeddings e JOIN memory_claims c ON c.id=e.memory_id
ON CONFLICT (tenant_id,model_id) DO NOTHING;

-- Dimension 384 is a prototype fixed-profile contract.
-- Changing embedding models or dimensions requires separate indexes/tables.

CREATE TABLE IF NOT EXISTS memory_edges (
  edge_id uuid PRIMARY KEY,
  tenant_id text NOT NULL,
  from_entity text NOT NULL,
  predicate text NOT NULL,
  to_entity text NOT NULL,
  source_memory_id uuid REFERENCES memory_claims(id),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  retired_at timestamptz
);
ALTER TABLE memory_edges ADD COLUMN IF NOT EXISTS retired_at timestamptz;
CREATE INDEX IF NOT EXISTS memory_edges_adj
ON memory_edges(tenant_id,from_entity,predicate,to_entity);

-- Observation timestamps are not database ingestion timestamps.
CREATE TABLE IF NOT EXISTS memory_observations (
  tenant_id text NOT NULL,
  device_id text NOT NULL,
  source_event_id text NOT NULL,
  observed_at timestamptz NOT NULL,
  ingested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  measured_value double precision NOT NULL,
  unit text NOT NULL,
  property text NOT NULL,
  source_id text NOT NULL,
  PRIMARY KEY(tenant_id,device_id,source_event_id)
);
CREATE INDEX IF NOT EXISTS memory_observations_lookup
ON memory_observations(tenant_id, device_id, property, observed_at DESC);


-- Canonical entity IDs and raw-source provenance.
CREATE TABLE IF NOT EXISTS memory_entities (
  tenant_id text NOT NULL,
  entity_id text NOT NULL,
  entity_type text NOT NULL CHECK (entity_type IN
    ('person','agent','organization','project','device','room','place','concept','account','document')),
  canonical_name text NOT NULL,
  aliases text[] NOT NULL DEFAULT '{}',
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant_id,entity_id)
);

CREATE TABLE IF NOT EXISTS memory_sources (
  tenant_id text NOT NULL,
  source_id text NOT NULL,
  source_type text NOT NULL CHECK (source_type IN
    ('conversation','document','sensor','tool','user_input','external')),
  uri text,
  object_sha256 text,
  observed_at timestamptz,
  ingested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  trust_level text NOT NULL DEFAULT 'unverified'
    CHECK (trust_level IN ('unverified','user_asserted','device_reported','independently_verified')),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY(tenant_id,source_id),
  CHECK (object_sha256 IS NULL OR length(object_sha256)=64)
);

-- Many-to-many provenance: one canonical claim may be supported by multiple sources.
CREATE TABLE IF NOT EXISTS memory_claim_sources (
  tenant_id text NOT NULL,
  claim_id uuid NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
  source_id text NOT NULL,
  confidence double precision NOT NULL DEFAULT 1.0
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
  linked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id,claim_id,source_id),
  FOREIGN KEY (tenant_id,source_id)
    REFERENCES memory_sources(tenant_id,source_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS memory_claim_sources_source
ON memory_claim_sources(tenant_id,source_id,claim_id);

CREATE TABLE IF NOT EXISTS memory_outbox (
  event_id uuid PRIMARY KEY,
  tenant_id text NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id text NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  processed_at timestamptz,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error text
);
CREATE INDEX IF NOT EXISTS memory_outbox_pending
  ON memory_outbox(available_at,created_at) WHERE processed_at IS NULL;

CREATE TABLE IF NOT EXISTS memory_idempotency (
  tenant_id text NOT NULL,
  idempotency_key text NOT NULL,
  request_sha256 text NOT NULL CHECK (length(request_sha256)=64),
  result jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant_id,idempotency_key)
);


-- Tenant-scoped ownership registry for content-addressed objects.
-- The underlying local/S3 object may be deduplicated globally, but access is
-- granted only to tenants with an explicit registry row.
CREATE TABLE IF NOT EXISTS memory_objects (
  tenant_id text NOT NULL,
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, sha256)
);
CREATE INDEX IF NOT EXISTS memory_objects_by_hash ON memory_objects(sha256);
