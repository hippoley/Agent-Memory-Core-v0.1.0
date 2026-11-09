-- Run with a PostgreSQL build that has Apache AGE installed.
-- This is a projection backend, NOT an alternative source of truth.
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, public;
SELECT create_graph('memory_graph');
-- Graph projection writes and replay/reconciliation are planned.
-- Do not write AGE graph edges as though they were synchronized automatically.
