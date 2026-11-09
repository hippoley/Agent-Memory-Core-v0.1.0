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


-- Tenant ownership metadata for content-addressed evidence objects.
-- Physical objects may share a digest, but access is granted per tenant.
CREATE TABLE IF NOT EXISTS memory_objects (
  tenant_id text NOT NULL,
  object_sha256 text NOT NULL CHECK (object_sha256 ~ '^[0-9a-f]{64}$'),
  byte_size bigint NOT NULL CHECK (byte_size >= 0),
  content_type text NOT NULL DEFAULT 'application/octet-stream',
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id, object_sha256)
);
CREATE INDEX IF NOT EXISTS memory_objects_created
ON memory_objects(tenant_id, created_at DESC);

-- Reliable outbox lifecycle. Additive so existing installations upgrade safely.
ALTER TABLE memory_outbox
  ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz;

CREATE INDEX IF NOT EXISTS memory_outbox_ready
  ON memory_outbox(event_type,available_at,created_at,event_id)
  WHERE processed_at IS NULL AND dead_lettered_at IS NULL;

CREATE INDEX IF NOT EXISTS memory_outbox_dead_letters
  ON memory_outbox(dead_lettered_at,created_at,event_id)
  WHERE dead_lettered_at IS NOT NULL AND processed_at IS NULL;


-- Independent, durable fan-out: one canonical event creates one task per projection.
CREATE TABLE IF NOT EXISTS memory_projection_jobs (
  event_id uuid NOT NULL,
  projection_name text NOT NULL,
  tenant_id text NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id text NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','retry','succeeded','dead_letter')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  last_attempt_at timestamptz,
  processed_at timestamptz,
  dead_lettered_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(event_id,projection_name)
);
CREATE INDEX IF NOT EXISTS memory_projection_jobs_ready
  ON memory_projection_jobs(projection_name,available_at,created_at,event_id)
  WHERE status IN ('pending','retry');
CREATE INDEX IF NOT EXISTS memory_projection_jobs_dead_letters
  ON memory_projection_jobs(projection_name,dead_lettered_at,created_at)
  WHERE status='dead_letter';

CREATE OR REPLACE FUNCTION enqueue_claim_projection_jobs()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.event_type = 'memory.claim.created' THEN
    INSERT INTO memory_projection_jobs
      (event_id,projection_name,tenant_id,aggregate_type,aggregate_id,event_type,payload)
    SELECT NEW.event_id, target, NEW.tenant_id, 'claim', NEW.event_id::text,
           NEW.event_type, NEW.payload
    FROM unnest(ARRAY['fts','vector','age']::text[]) AS target
    ON CONFLICT(event_id,projection_name) DO NOTHING;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS memory_outbox_projection_fanout ON memory_outbox;
CREATE TRIGGER memory_outbox_projection_fanout
AFTER INSERT ON memory_outbox
FOR EACH ROW EXECUTE FUNCTION enqueue_claim_projection_jobs();


-- First concrete text projection. pg_trgm provides substring matching for CJK text
-- where the built-in simple text-search parser does not perform word segmentation.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS memory_fts_documents (
  tenant_id text NOT NULL,
  memory_id uuid NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
  document_text text NOT NULL,
  search_vector tsvector NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant_id,memory_id)
);
CREATE INDEX IF NOT EXISTS memory_fts_documents_vector
  ON memory_fts_documents USING gin(search_vector);
CREATE INDEX IF NOT EXISTS memory_fts_documents_trgm
  ON memory_fts_documents USING gin(document_text gin_trgm_ops);


-- Explicit assertion polarity preserves negation without inverting the fact value.
ALTER TABLE memory_claims
  ADD COLUMN IF NOT EXISTS polarity text NOT NULL DEFAULT 'positive';
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='memory_claims_polarity_check'
      AND conrelid='memory_claims'::regclass
  ) THEN
    ALTER TABLE memory_claims
      ADD CONSTRAINT memory_claims_polarity_check
      CHECK (polarity IN ('positive','negative'));
  END IF;
END;
$$;


-- Idempotency records are committed atomically with canonical claim creation.
CREATE TABLE IF NOT EXISTS memory_idempotency_keys (
  tenant_id text NOT NULL,
  idempotency_key text NOT NULL,
  request_sha256 char(64) NOT NULL,
  memory_id uuid NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(tenant_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS memory_idempotency_created
  ON memory_idempotency_keys(created_at);
