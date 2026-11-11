-- S11: tenant-scoped trusted predicate cardinality.
-- Absent policy rows intentionally default to single-valued in application code.
CREATE TABLE IF NOT EXISTS memory_predicate_policies (
  tenant_id text NOT NULL,
  predicate text NOT NULL CHECK (length(predicate) BETWEEN 1 AND 100),
  cardinality text NOT NULL CHECK (cardinality IN ('one','many')),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (tenant_id,predicate)
);
