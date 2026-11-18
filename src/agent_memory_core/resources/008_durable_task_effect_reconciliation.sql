-- Durable external-effect execution and ambiguity reconciliation.
-- Existing effect intents are backfilled from the transactional Outbox.
BEGIN;

ALTER TABLE memory_task_effects
  DROP CONSTRAINT IF EXISTS memory_task_effects_status_check;

ALTER TABLE memory_task_effects
  ADD CONSTRAINT memory_task_effects_status_check
  CHECK (status IN ('requested','executing','unknown','reconciling','committed','failed'));

ALTER TABLE memory_task_effects
  ADD COLUMN IF NOT EXISTS request jsonb,
  ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0
    CHECK (attempt_count >= 0),
  ADD COLUMN IF NOT EXISTS execution_worker_id text,
  ADD COLUMN IF NOT EXISTS execution_lease_epoch bigint,
  ADD COLUMN IF NOT EXISTS execution_started_at timestamptz,
  ADD COLUMN IF NOT EXISTS provider_reference text,
  ADD COLUMN IF NOT EXISTS provider_result jsonb,
  ADD COLUMN IF NOT EXISTS last_error jsonb,
  ADD COLUMN IF NOT EXISTS resolved_at timestamptz;

-- The request payload was already committed atomically to the Outbox in
-- migration 006. Recover it for intents created before this migration.
UPDATE memory_task_effects AS effect
SET request = event.payload->'request'
FROM memory_outbox AS event
WHERE effect.request IS NULL
  AND event.event_type='memory.task.effect.requested'
  AND event.aggregate_id=effect.task_id::text
  AND event.payload->>'effect_key'=effect.effect_key;


DO $migration008$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='memory_task_effects_execution_owner_shape'
      AND conrelid='memory_task_effects'::regclass
  ) THEN
    ALTER TABLE memory_task_effects
      ADD CONSTRAINT memory_task_effects_execution_owner_shape
      CHECK (
        (execution_worker_id IS NULL AND execution_lease_epoch IS NULL)
        OR
        (execution_worker_id IS NOT NULL AND execution_lease_epoch IS NOT NULL)
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname='memory_task_effects_status_owner_shape'
      AND conrelid='memory_task_effects'::regclass
  ) THEN
    ALTER TABLE memory_task_effects
      ADD CONSTRAINT memory_task_effects_status_owner_shape
      CHECK (
        (status IN ('executing','reconciling')) =
        (execution_worker_id IS NOT NULL)
      );
  END IF;
END
$migration008$;

CREATE INDEX IF NOT EXISTS memory_task_effects_recovery
  ON memory_task_effects(status,updated_at)
  WHERE status IN ('executing','unknown','reconciling');

CREATE INDEX IF NOT EXISTS memory_task_effects_worker_epoch
  ON memory_task_effects(task_id,execution_lease_epoch)
  WHERE status IN ('executing','reconciling');

COMMENT ON COLUMN memory_task_effects.status IS
  'External effect lifecycle. executing with a lost task lease becomes unknown; unknown must be reconciled before retry.';
COMMENT ON COLUMN memory_task_effects.request IS
  'Canonical provider request, copied from the transactional effect-requested Outbox event.';
COMMENT ON COLUMN memory_task_effects.execution_lease_epoch IS
  'Task lease epoch that initiated the current external operation; stale epochs cannot commit its outcome.';

REVOKE ALL ON memory_task_effects FROM PUBLIC;
COMMIT;
