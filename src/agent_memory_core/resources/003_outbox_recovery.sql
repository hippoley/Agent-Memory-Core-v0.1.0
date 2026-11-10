-- Bounded retry and explicit dead-letter state for the transactional outbox.
ALTER TABLE memory_outbox
  ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz;
ALTER TABLE memory_outbox
  ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 12
  CHECK (max_attempts > 0);
CREATE INDEX IF NOT EXISTS memory_outbox_dead_letters
  ON memory_outbox(dead_lettered_at,created_at)
  WHERE dead_lettered_at IS NOT NULL;
