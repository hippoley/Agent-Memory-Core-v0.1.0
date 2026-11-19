-- Durable task runtime: PostgreSQL is the canonical business state.
-- Temporal owns orchestration history; this schema owns task identity,
-- checkpoint versions, and idempotent effect intents.
BEGIN;

CREATE TABLE IF NOT EXISTS memory_durable_tasks (
  task_id uuid PRIMARY KEY,
  tenant_id text NOT NULL,
  created_by_principal_id uuid,
  task_type text NOT NULL CHECK (length(task_type) BETWEEN 1 AND 120),
  workflow_id text,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','running','paused','completed','failed','cancelled')),
  input jsonb NOT NULL,
  result jsonb,
  checkpoint_version bigint NOT NULL DEFAULT 0 CHECK (checkpoint_version >= 0),
  progress_completed bigint NOT NULL DEFAULT 0 CHECK (progress_completed >= 0),
  progress_total bigint NOT NULL DEFAULT 0 CHECK (progress_total >= 0),
  idempotency_key text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  started_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz,
  last_error jsonb,
  CHECK (progress_total = 0 OR progress_completed <= progress_total),
  CHECK ((status IN ('completed','failed','cancelled')) = (completed_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS memory_durable_tasks_idempotency
  ON memory_durable_tasks(tenant_id,idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS memory_durable_tasks_tenant_recent
  ON memory_durable_tasks(tenant_id,created_at DESC,task_id);
CREATE INDEX IF NOT EXISTS memory_durable_tasks_workflow
  ON memory_durable_tasks(tenant_id,workflow_id)
  WHERE workflow_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS memory_task_checkpoints (
  task_id uuid NOT NULL REFERENCES memory_durable_tasks(task_id) ON DELETE CASCADE,
  version bigint NOT NULL CHECK (version > 0),
  checkpoint jsonb NOT NULL,
  artifact_sha256 text,
  idempotency_key text,
  progress_completed bigint NOT NULL DEFAULT 0 CHECK (progress_completed >= 0),
  progress_total bigint NOT NULL DEFAULT 0 CHECK (progress_total >= 0),
  effect_key text,
  effect_request_sha256 text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(task_id,version),
  UNIQUE(task_id,idempotency_key),
  CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
  CHECK (progress_total = 0 OR progress_completed <= progress_total),
  CHECK ((effect_key IS NULL) = (effect_request_sha256 IS NULL)),
  CHECK (effect_request_sha256 IS NULL OR effect_request_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS memory_task_checkpoints_recent
  ON memory_task_checkpoints(task_id,version DESC);

CREATE TABLE IF NOT EXISTS memory_task_effects (
  task_id uuid NOT NULL REFERENCES memory_durable_tasks(task_id) ON DELETE CASCADE,
  effect_key text NOT NULL CHECK (length(effect_key) BETWEEN 1 AND 255),
  request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  status text NOT NULL DEFAULT 'requested'
    CHECK (status IN ('requested','committed','failed')),
  result jsonb,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(task_id,effect_key)
);
CREATE INDEX IF NOT EXISTS memory_task_effects_status
  ON memory_task_effects(task_id,status,created_at);

REVOKE ALL ON memory_durable_tasks,memory_task_checkpoints,memory_task_effects FROM PUBLIC;
COMMIT;
