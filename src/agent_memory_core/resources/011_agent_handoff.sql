-- US-C01: immutable, tenant-scoped agent work handoff snapshots.
BEGIN;
CREATE TABLE IF NOT EXISTS memory_agent_handoffs (
  handoff_id uuid PRIMARY KEY,
  tenant_id text NOT NULL,
  task_id uuid NOT NULL REFERENCES memory_durable_tasks(task_id) ON DELETE RESTRICT,
  source_agent text NOT NULL CHECK (length(source_agent) BETWEEN 1 AND 120),
  target_agent text NOT NULL CHECK (length(target_agent) BETWEEN 1 AND 120),
  payload jsonb NOT NULL,
  payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  status text NOT NULL DEFAULT 'offered' CHECK (status IN ('offered','accepted')),
  accepted_by text,
  accepted_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (tenant_id,handoff_id),
  CHECK ((status='offered' AND accepted_at IS NULL AND accepted_by IS NULL)
      OR (status='accepted' AND accepted_at IS NOT NULL AND accepted_by IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS memory_agent_handoffs_task
  ON memory_agent_handoffs (tenant_id,task_id,created_at DESC);
REVOKE ALL ON memory_agent_handoffs FROM PUBLIC;
COMMIT;
