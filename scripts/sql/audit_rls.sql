-- audit_rls.sql -- READ-ONLY. Changes nothing.
--
-- Supabase exposes every table in the `public` schema through its auto-generated
-- PostgREST API at https://<project-ref>.supabase.co/rest/v1/<table>, and its
-- default privileges grant the `anon` and `authenticated` roles access to tables
-- created in that schema. SQLAlchemy's create_all does not enable Row Level
-- Security, so tables it created may be readable and writable by anyone holding
-- the anon key -- which is public by design, since it ships in frontend bundles.
--
-- This backend does not use PostgREST at all: it connects over the Postgres
-- protocol as the table owner and enforces authorization in application code.
-- So anon/authenticated access to these tables is pure exposure with no upside.
--
-- Run in Supabase Dashboard > SQL Editor and read the output.

-- Per-table exposure. A row with rls_enabled = false AND anon_can_select = true
-- is readable by anyone with the (public) anon key.
SELECT c.relname                                            AS table_name,
       c.relowner::regrole                                  AS owner,
       c.relrowsecurity                                     AS rls_enabled,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies,
       has_table_privilege('anon', c.oid, 'SELECT')         AS anon_can_select,
       has_table_privilege('anon', c.oid, 'INSERT')         AS anon_can_insert,
       has_table_privilege('anon', c.oid, 'UPDATE')         AS anon_can_update,
       has_table_privilege('anon', c.oid, 'DELETE')         AS anon_can_delete
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
ORDER BY c.relname;

-- Does the role the backend connects as bypass RLS? Table owners bypass RLS by
-- default unless FORCE ROW LEVEL SECURITY is set, and rolbypassrls makes it
-- unconditional. Confirm this is true before enabling RLS, so the backend keeps
-- full access to its own tables.
SELECT rolname, rolsuper, rolbypassrls
FROM pg_roles
WHERE rolname IN ('postgres', 'anon', 'authenticated', 'service_role')
ORDER BY rolname;

-- Who recorded the default privileges that newly created tables inherit?
-- ALTER DEFAULT PRIVILEGES only edits the entries belonging to the role running
-- it, so any row here granted by a role other than the one you connect as needs
-- its own FOR ROLE statement to clear.
SELECT d.defaclrole::regrole AS granted_by,
       n.nspname             AS schema,
       CASE d.defaclobjtype
           WHEN 'r' THEN 'table'
           WHEN 'S' THEN 'sequence'
           WHEN 'f' THEN 'function'
           WHEN 'T' THEN 'type'
           WHEN 'n' THEN 'schema'
           ELSE d.defaclobjtype::text
       END                   AS object_type,
       d.defaclacl           AS access_privileges
FROM pg_default_acl d
LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace
ORDER BY granted_by, schema, object_type;
