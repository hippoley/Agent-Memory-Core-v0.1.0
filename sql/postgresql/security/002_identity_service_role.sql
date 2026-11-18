-- Provision the application control-plane role after migration 005.
-- Run as database administrator. This role must never be granted to memory_tenant
-- or to a tenant-specific data-plane login.
BEGIN;
DO $role$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='memory_identity_service') THEN
    CREATE ROLE memory_identity_service NOLOGIN INHERIT NOBYPASSRLS;
  END IF;
END
$role$;

GRANT USAGE ON SCHEMA public TO memory_identity_service;

GRANT SELECT,INSERT,UPDATE ON
  public.memory_principals,
  public.memory_external_identities,
  public.memory_tenants,
  public.memory_tenant_memberships,
  public.memory_sessions,
  public.memory_api_credentials,
  public.memory_identity_audit_events
TO memory_identity_service;

-- Example operator provisioning (execute separately; use a secret manager):
-- CREATE ROLE memory_identity_app LOGIN PASSWORD '<unique-secret>';
-- GRANT memory_identity_service TO memory_identity_app;
-- GRANT CONNECT ON DATABASE memory TO memory_identity_app;
-- GRANT USAGE ON SCHEMA public TO memory_identity_service;
COMMIT;
