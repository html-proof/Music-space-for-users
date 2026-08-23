-- 002_lock_down_public_schema.sql
--
-- Closes PostgREST access to this project's tables.
--
-- Context: Supabase serves every table in the `public` schema over its
-- auto-generated REST API, and its default privileges grant the `anon` and
-- `authenticated` roles access to tables created there. SQLAlchemy's
-- create_all() does not enable Row Level Security, so tables it created are
-- reachable by anyone holding the anon key -- which is public by design, since
-- it ships inside frontend bundles.
--
-- This backend never uses PostgREST. It connects over the Postgres protocol as
-- the table owner and enforces authorization in application code
-- (app/middleware/firebase_auth.py), so anon access is exposure with no upside.
--
-- ENABLE ROW LEVEL SECURITY is the load-bearing control here: with RLS on and no
-- permissive policy, every non-bypassing role is denied every row regardless of
-- what GRANTs exist. The REVOKEs are hygiene on top of that.
--
-- Run RUNBOOK step 1 (audit_rls.sql) first. Then run this whole file at once --
-- it is wrapped in a transaction, so nothing lands unless all of it succeeds.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Turn on RLS for every table in `public`.
--
--    With no policy attached, this denies all access to roles that do not
--    bypass RLS. Table owners bypass it by default (absent FORCE ROW LEVEL
--    SECURITY), which is why the backend keeps working -- confirm the owner and
--    rolbypassrls columns from audit_rls.sql before running this.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t text;
BEGIN
    FOR t IN
        SELECT tablename FROM pg_tables WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        RAISE NOTICE 'RLS enabled on public.%', t;
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. Remove the standing grants to the two web-facing roles.
-- ---------------------------------------------------------------------------
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. Stop future tables from inheriting those grants.
--
--    ALTER DEFAULT PRIVILEGES only affects defaults recorded for the role that
--    runs it. If the audit's pg_default_acl query shows entries granted by a
--    different role (e.g. supabase_admin), those need their own statement with
--    FOR ROLE, or they will keep applying to newly created tables. Step 1 still
--    protects those tables, because create_all does not create policies.
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;

COMMIT;

-- ---------------------------------------------------------------------------
-- 4. Verify. Every row should now read rls_enabled = t, anon_can_select = f.
-- ---------------------------------------------------------------------------
SELECT c.relname                                    AS table_name,
       c.relrowsecurity                             AS rls_enabled,
       has_table_privilege('anon', c.oid, 'SELECT')  AS anon_can_select,
       has_table_privilege('anon', c.oid, 'INSERT')  AS anon_can_insert
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
ORDER BY c.relname;

-- ---------------------------------------------------------------------------
-- ROLLBACK, if the backend turns out to be affected.
--
--   DO $$
--   DECLARE t text;
--   BEGIN
--       FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public'
--       LOOP
--           EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', t);
--       END LOOP;
--   END
--   $$;
--   GRANT ALL ON ALL TABLES    IN SCHEMA public TO anon, authenticated;
--   GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
--
-- Restoring the grants puts the exposure back. Prefer diagnosing over rolling
-- back: if the backend breaks, the cause is almost certainly that it connects as
-- a role which neither owns the tables nor has rolbypassrls.
-- ---------------------------------------------------------------------------
