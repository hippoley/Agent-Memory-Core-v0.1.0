-- Optional administrator-only source authority roles.
-- NOT part of PostgresMemory.migrate(): requires CREATE ROLE privileges.
-- Do not grant these roles to tenant logins, public API/LLM runtimes, or
-- memory_identity_service. Only a separately authenticated verifier may read.
BEGIN;
DO $roles$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_source_verifier') THEN
    CREATE ROLE memory_source_verifier NOLOGIN NOINHERIT NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_source_authority') THEN
    CREATE ROLE memory_source_authority NOLOGIN NOINHERIT NOBYPASSRLS;
  END IF;
END
$roles$;

-- Both are control-plane roles. The verifier can read the minimum needed
-- issuer/tenant/service status, but cannot create or mutate trust policy.
GRANT USAGE ON SCHEMA public TO memory_source_verifier;
GRANT SELECT ON public.memory_knowledge_source_revisions,
  public.memory_evidence_producer_keys,
  public.memory_principals,
  public.memory_tenants
TO memory_source_verifier;

-- Revocation/registration is operator-controlled; never give the
-- verifier the same role or the operator's credentials.
GRANT USAGE ON SCHEMA public TO memory_source_authority;
GRANT SELECT, INSERT, UPDATE ON public.memory_knowledge_source_revisions
TO memory_source_authority;
GRANT SELECT ON public.memory_evidence_producer_keys,
  public.memory_principals, public.memory_tenants
TO memory_source_authority;

-- Explicitly assert the data-plane role cannot inherit any trust authority.
DO $assert$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_tenant') THEN
    EXECUTE 'REVOKE ALL ON public.memory_knowledge_source_revisions FROM memory_tenant';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_identity_service') THEN
    EXECUTE 'REVOKE ALL ON public.memory_knowledge_source_revisions FROM memory_identity_service';
  END IF;
END
$assert$;
COMMIT;

-- Operator provisioning example (outside this script):
-- CREATE ROLE memory_source_verify_app LOGIN PASSWORD '<secret>' NOINHERIT;
-- GRANT memory_source_verifier TO memory_source_verify_app;
-- A separate, more privileged administrator must register/revoke issuers.
