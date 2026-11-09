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
CREATE TABLE IF NOT EXISTS memory_embeddings (
  memory_id uuid NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
  model_id text NOT NULL,
  embedding vector(384) NOT NULL,
  PRIMARY KEY(memory_id,model_id)
);
CREATE INDEX IF NOT EXISTS memory_embeddings_hnsw
ON memory_embeddings USING hnsw (embedding vector_cosine_ops);
-- Dimension 384 is a prototype fixed-profile contract.
-- Changing embedding models or dimensions requires separate indexes/tables.

CREATE TABLE IF NOT EXISTS memory_edges (
  edge_id uuid PRIMARY KEY,
  tenant_id text NOT NULL,
  from_entity text NOT NULL,
  predicate text NOT NULL,
  to_entity text NOT NULL,
  source_memory_id uuid REFERENCES memory_claims(id),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
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
