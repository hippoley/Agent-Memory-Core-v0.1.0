-- Database-enforced tenant isolation for API connections.
-- API connections SET ROLE memory_app and set app.tenant_id per request.
-- Background workers SET ROLE memory_worker; that role is intentionally cross-tenant.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_app') THEN
    CREATE ROLE memory_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_worker') THEN
    CREATE ROLE memory_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO memory_app,memory_worker;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO memory_app,memory_worker;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO memory_app,memory_worker;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'memory_claims','memory_events','memory_embedding_models',
    'memory_edges','memory_observations','memory_entities','memory_sources',
    'memory_claim_sources','memory_outbox','memory_idempotency','memory_objects',
    'memory_claim_search','memory_webhook_subscriptions','memory_webhook_deliveries'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_scope ON public.%I',table_name);
    EXECUTE format(
      'CREATE POLICY tenant_scope ON public.%I USING ' ||
      '(current_user = ''memory_worker'' OR tenant_id = NULLIF(current_setting(''app.tenant_id'',true),'''')) ' ||
      'WITH CHECK (current_user = ''memory_worker'' OR tenant_id = NULLIF(current_setting(''app.tenant_id'',true),''''))',
      table_name);
  END LOOP;
END $$;

-- These projections have no tenant_id column of their own. Ownership is
-- derived from the canonical claim/outbox/edge, which remains authoritative.
ALTER TABLE memory_embeddings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON memory_embeddings;
CREATE POLICY tenant_scope ON memory_embeddings
  USING (current_user='memory_worker' OR EXISTS (
    SELECT 1 FROM memory_claims c
    WHERE c.id=memory_embeddings.memory_id
      AND c.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')
  ))
  WITH CHECK (current_user='memory_worker' OR EXISTS (
    SELECT 1 FROM memory_claims c
    WHERE c.id=memory_embeddings.memory_id
      AND c.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')
  ));

ALTER TABLE memory_embeddings_512 ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON memory_embeddings_512;
CREATE POLICY tenant_scope ON memory_embeddings_512
  USING (current_user='memory_worker' OR EXISTS (
    SELECT 1 FROM memory_claims c
    WHERE c.id=memory_embeddings_512.memory_id
      AND c.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')
  ))
  WITH CHECK (current_user='memory_worker' OR EXISTS (
    SELECT 1 FROM memory_claims c
    WHERE c.id=memory_embeddings_512.memory_id
      AND c.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')
  ));

ALTER TABLE memory_projection_deliveries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON memory_projection_deliveries;
CREATE POLICY tenant_scope ON memory_projection_deliveries
  USING (current_user='memory_worker' OR EXISTS (
    SELECT 1 FROM memory_outbox o
    WHERE o.event_id=memory_projection_deliveries.event_id
      AND o.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')
  ))
  WITH CHECK (current_user='memory_worker' OR EXISTS (
    SELECT 1 FROM memory_outbox o
    WHERE o.event_id=memory_projection_deliveries.event_id
      AND o.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')
  ));

DO $$
BEGIN
  IF to_regclass('public.memory_graph_projection') IS NOT NULL THEN
    ALTER TABLE public.memory_graph_projection ENABLE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS tenant_scope ON public.memory_graph_projection;
    CREATE POLICY tenant_scope ON public.memory_graph_projection
      USING (current_user='memory_worker' OR EXISTS (
        SELECT 1 FROM memory_edges e
        WHERE e.edge_id=memory_graph_projection.edge_id
          AND e.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')
      ))
      WITH CHECK (current_user='memory_worker' OR EXISTS (
        SELECT 1 FROM memory_edges e
        WHERE e.edge_id=memory_graph_projection.edge_id
          AND e.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')
      ));
  END IF;
END $$;
