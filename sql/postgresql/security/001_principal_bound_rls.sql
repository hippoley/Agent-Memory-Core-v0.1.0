-- S10: opt-in, operator-only RLS installation.
-- NEVER put this file in automatic tenant/API migrations. It needs admin rights.
-- Authenticate every tenant using a DISTINCT PostgreSQL LOGIN role. Grant
-- memory_tenant to that login and map its session_user in the private registry.
-- No mutable app.tenant_id custom setting is consulted in any RLS policy.
BEGIN;
DO $setup$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_tenant') THEN
    CREATE ROLE memory_tenant NOLOGIN INHERIT NOBYPASSRLS;
  END IF;
END
$setup$;

CREATE TABLE IF NOT EXISTS public.memory_rls_principals (
  role_name name PRIMARY KEY,
  tenant_id text NOT NULL UNIQUE CHECK (tenant_id<>'')
);
REVOKE ALL ON public.memory_rls_principals FROM PUBLIC;
REVOKE ALL ON public.memory_rls_principals FROM memory_tenant;

-- Executed as a protected SECURITY DEFINER function. session_user is the
-- authenticated LOGIN principal, not a freely mutable custom GUC.
-- The admin/migration role that owns this function must not be exposed to tenants.
CREATE OR REPLACE FUNCTION public.memory_authenticated_tenant()
RETURNS text LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public
AS $fn$
  SELECT p.tenant_id
  FROM public.memory_rls_principals AS p
  WHERE p.role_name=session_user::name
$fn$;
REVOKE ALL ON FUNCTION public.memory_authenticated_tenant() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.memory_authenticated_tenant() TO memory_tenant;
GRANT USAGE ON SCHEMA public TO memory_tenant;

-- The tenant role is deliberately NOT a table owner or RLS-bypassing role.
-- Never grant membership in a privileged worker or migration role to it.
DO $policies$
DECLARE
  tab text;
BEGIN
  FOREACH tab IN ARRAY ARRAY[
    'memory_claims','memory_events','memory_embedding_models',
    'memory_edges','memory_observations','memory_entities','memory_sources',
    'memory_claim_sources','memory_outbox','memory_idempotency','memory_objects',
    'memory_claim_search','memory_webhook_subscriptions','memory_predicate_policies'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',tab);
    EXECUTE format('DROP POLICY IF EXISTS memory_authenticated_tenant_policy ON public.%I',tab);
    EXECUTE format(
      'CREATE POLICY memory_authenticated_tenant_policy ON public.%I ' ||
      'USING (tenant_id=public.memory_authenticated_tenant()) ' ||
      'WITH CHECK (tenant_id=public.memory_authenticated_tenant())',tab);
    EXECUTE format(
      'GRANT SELECT,INSERT,UPDATE,DELETE ON public.%I TO memory_tenant',tab);
  END LOOP;
END
$policies$;

-- Predicate cardinality is trusted tenant configuration. Tenant API roles
-- may read their policy but cannot change it through direct SQL.
REVOKE INSERT,UPDATE,DELETE ON public.memory_predicate_policies FROM memory_tenant;

-- Derived tables without a tenant_id are guarded through canonical parent.
ALTER TABLE public.memory_embeddings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS memory_authenticated_tenant_policy ON public.memory_embeddings;
CREATE POLICY memory_authenticated_tenant_policy ON public.memory_embeddings
  USING (EXISTS (
    SELECT 1 FROM public.memory_claims c
    WHERE c.id=memory_embeddings.memory_id
      AND c.tenant_id=public.memory_authenticated_tenant()))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.memory_claims c
    WHERE c.id=memory_embeddings.memory_id
      AND c.tenant_id=public.memory_authenticated_tenant()));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.memory_embeddings TO memory_tenant;

ALTER TABLE public.memory_embeddings_512 ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS memory_authenticated_tenant_policy ON public.memory_embeddings_512;
CREATE POLICY memory_authenticated_tenant_policy ON public.memory_embeddings_512
  USING (EXISTS (
    SELECT 1 FROM public.memory_claims c
    WHERE c.id=memory_embeddings_512.memory_id
      AND c.tenant_id=public.memory_authenticated_tenant()))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.memory_claims c
    WHERE c.id=memory_embeddings_512.memory_id
      AND c.tenant_id=public.memory_authenticated_tenant()));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.memory_embeddings_512 TO memory_tenant;

ALTER TABLE public.memory_projection_deliveries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS memory_authenticated_tenant_policy ON public.memory_projection_deliveries;
CREATE POLICY memory_authenticated_tenant_policy ON public.memory_projection_deliveries
  USING (EXISTS (
    SELECT 1 FROM public.memory_outbox o
    WHERE o.event_id=memory_projection_deliveries.event_id
      AND o.tenant_id=public.memory_authenticated_tenant()))
  WITH CHECK (EXISTS (
    SELECT 1 FROM public.memory_outbox o
    WHERE o.event_id=memory_projection_deliveries.event_id
      AND o.tenant_id=public.memory_authenticated_tenant()));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.memory_projection_deliveries TO memory_tenant;

-- Both referenced entities must belong to the authenticated tenant.
ALTER TABLE public.memory_webhook_deliveries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS memory_authenticated_tenant_policy ON public.memory_webhook_deliveries;
CREATE POLICY memory_authenticated_tenant_policy ON public.memory_webhook_deliveries
  USING (
    tenant_id=public.memory_authenticated_tenant()
    AND EXISTS (SELECT 1 FROM public.memory_outbox o
                WHERE o.event_id=memory_webhook_deliveries.event_id
                AND o.tenant_id=public.memory_authenticated_tenant())
    AND EXISTS (SELECT 1 FROM public.memory_webhook_subscriptions s
                WHERE s.subscription_id=memory_webhook_deliveries.subscription_id
                AND s.tenant_id=public.memory_authenticated_tenant()))
  WITH CHECK (
    tenant_id=public.memory_authenticated_tenant()
    AND EXISTS (SELECT 1 FROM public.memory_outbox o
                WHERE o.event_id=memory_webhook_deliveries.event_id
                AND o.tenant_id=public.memory_authenticated_tenant())
    AND EXISTS (SELECT 1 FROM public.memory_webhook_subscriptions s
                WHERE s.subscription_id=memory_webhook_deliveries.subscription_id
                AND s.tenant_id=public.memory_authenticated_tenant()));
GRANT SELECT,INSERT,UPDATE,DELETE ON public.memory_webhook_deliveries TO memory_tenant;

-- Cross-tenant reference integrity for tenant-id-bearing relational tables.
-- These constraints deliberately fail installation if legacy data is invalid.
CREATE UNIQUE INDEX IF NOT EXISTS memory_claims_tenant_id_id_unique
  ON public.memory_claims(tenant_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS memory_outbox_tenant_event_unique
  ON public.memory_outbox(tenant_id,event_id);
CREATE UNIQUE INDEX IF NOT EXISTS memory_webhook_sub_tenant_unique
  ON public.memory_webhook_subscriptions(tenant_id,subscription_id);
DO $constraints$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='memory_claim_replacement_same_tenant_fk') THEN
    ALTER TABLE public.memory_claims ADD CONSTRAINT memory_claim_replacement_same_tenant_fk
      FOREIGN KEY (tenant_id,replaces_id) REFERENCES public.memory_claims(tenant_id,id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='memory_claim_sources_same_tenant_fk') THEN
    ALTER TABLE public.memory_claim_sources ADD CONSTRAINT memory_claim_sources_same_tenant_fk
      FOREIGN KEY (tenant_id,claim_id) REFERENCES public.memory_claims(tenant_id,id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='memory_edges_same_tenant_source_fk') THEN
    ALTER TABLE public.memory_edges ADD CONSTRAINT memory_edges_same_tenant_source_fk
      FOREIGN KEY (tenant_id,source_memory_id) REFERENCES public.memory_claims(tenant_id,id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='memory_claim_search_same_tenant_fk') THEN
    ALTER TABLE public.memory_claim_search ADD CONSTRAINT memory_claim_search_same_tenant_fk
      FOREIGN KEY (tenant_id,memory_id) REFERENCES public.memory_claims(tenant_id,id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='memory_webhook_delivery_sub_same_tenant_fk') THEN
    ALTER TABLE public.memory_webhook_deliveries ADD CONSTRAINT memory_webhook_delivery_sub_same_tenant_fk
      FOREIGN KEY (tenant_id,subscription_id) REFERENCES public.memory_webhook_subscriptions(tenant_id,subscription_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                 WHERE conname='memory_webhook_delivery_event_same_tenant_fk') THEN
    ALTER TABLE public.memory_webhook_deliveries ADD CONSTRAINT memory_webhook_delivery_event_same_tenant_fk
      FOREIGN KEY (tenant_id,event_id) REFERENCES public.memory_outbox(tenant_id,event_id);
  END IF;
END
$constraints$;
COMMIT;
