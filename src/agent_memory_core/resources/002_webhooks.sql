-- Durable, tenant-scoped outbound webhook subscriptions and per-event deliveries.
CREATE TABLE IF NOT EXISTS memory_webhook_subscriptions (
  subscription_id uuid PRIMARY KEY,
  tenant_id text NOT NULL,
  target_url text NOT NULL,
  event_types text[] NOT NULL CHECK (cardinality(event_types) > 0),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  revoked_at timestamptz,
  CHECK ((active AND revoked_at IS NULL) OR (NOT active))
);
CREATE INDEX IF NOT EXISTS memory_webhook_subscriptions_active
  ON memory_webhook_subscriptions(tenant_id, active);

CREATE TABLE IF NOT EXISTS memory_webhook_deliveries (
  delivery_id uuid PRIMARY KEY,
  subscription_id uuid NOT NULL REFERENCES memory_webhook_subscriptions(subscription_id),
  event_id uuid NOT NULL REFERENCES memory_outbox(event_id) ON DELETE CASCADE,
  tenant_id text NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','delivering','delivered','dead')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  lease_until timestamptz,
  delivered_at timestamptz,
  response_status integer,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(subscription_id,event_id),
  CHECK ((status='delivered') = (delivered_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS memory_webhook_deliveries_pending
  ON memory_webhook_deliveries(available_at,created_at)
  WHERE status IN ('pending','delivering');
