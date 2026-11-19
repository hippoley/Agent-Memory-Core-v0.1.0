-- Durable Temporal dispatch ledger. PostgreSQL owns dispatch intent and identity.
BEGIN;
CREATE TABLE IF NOT EXISTS memory_temporal_dispatches (
  task_id uuid PRIMARY KEY REFERENCES memory_durable_tasks(task_id) ON DELETE CASCADE,
  tenant_id text NOT NULL,
  workflow_id text NOT NULL,
  task_queue text NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','dispatching','started','dead')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  lease_owner text,
  lease_epoch bigint NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
  lease_expires_at timestamptz,
  temporal_run_id text,
  started_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(tenant_id,workflow_id),
  CHECK ((status='dispatching') = (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)),
  CHECK ((status='started') = (started_at IS NOT NULL AND temporal_run_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS memory_temporal_dispatch_pending
  ON memory_temporal_dispatches(status,created_at)
  WHERE status IN ('pending','dispatching');
REVOKE ALL ON memory_temporal_dispatches FROM PUBLIC;
COMMIT;
