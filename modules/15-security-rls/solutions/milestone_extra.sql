-- Module 15 milestone: the two requirements that go beyond the reference
-- project/db/1501_schema_security.sql. Apply after 1501 (state 16):
--
--   docker exec -i course-pg psql -U postgres -d bazaar -v ON_ERROR_STOP=1 < modules/15-security-rls/solutions/milestone_extra.sql
BEGIN;

-- 1. Analysts must not read e-mail addresses. A column-level REVOKE is a no-op
--    while a table-level SELECT exists, so replace the table grant by column grants.
REVOKE SELECT ON bazaar.users FROM bazaar_readonly;
GRANT SELECT (id, full_name, is_active, created_at) ON bazaar.users TO bazaar_readonly;

-- 2. The audit trail is append-only, and only through the SECURITY DEFINER
--    audit_trigger(): the app may read it but not write, change or delete it.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON bazaar.audit_log FROM bazaar_app;

COMMIT;
