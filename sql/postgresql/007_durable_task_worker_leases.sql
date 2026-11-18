-- Worker ownership and fencing for PostgreSQL-backed durable tasks.
BEGIN;

ALTER TABLE memory_durable_tasks
  ADD COLUMN IF NOT EXISTS lease_owner text,
  ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS lease_epoch bigint NOT NULL DEFAULT 0
    CHECK (lease_epoch >= 0),
  ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;

ALTER TABLE memory_durable_tasks
  ADD CONSTRAINT memory_durable_tasks_lease_shape
  CHECK (
    (lease_owner IS NULL AND lease_expires_at IS NULL)
    OR
    (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
  );

CREATE INDEX IF NOT EXISTS memory_durable_tasks_claimable
  ON memory_durable_tasks(tenant_id,status,lease_expires_at,created_at)
  WHERE status IN ('queued','running');

CREATE INDEX IF NOT EXISTS memory_durable_tasks_lease_expiry
  ON memory_durable_tasks(lease_expires_at)
  WHERE lease_owner IS NOT NULL;

COMMENT ON COLUMN memory_durable_tasks.lease_epoch IS
  'Monotonically increasing fencing token. Every successful claim increments it.';
COMMENT ON COLUMN memory_durable_tasks.lease_owner IS
  'Unique worker process identity holding the current task lease.';

REVOKE ALL ON memory_durable_tasks FROM PUBLIC;
COMMIT;
