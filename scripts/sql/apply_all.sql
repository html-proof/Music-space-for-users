-- apply_all.sql -- paste this whole file into Supabase Dashboard > SQL Editor and press Run.
--
-- Combines 001_playlist_song_unique.sql and 002_lock_down_public_schema.sql into
-- one transaction, and adds the safety checks that were previously manual steps.
-- You do not need to read the output first or decide anything: PART 0 inspects the
-- database and aborts the whole run with an explanatory message if applying the
-- rest would lock the backend out of its own tables.
--
-- Everything is inside BEGIN/COMMIT, so the database either ends up fully
-- migrated or completely untouched. Re-running it is safe (idempotent).
--
-- WHAT IT CHANGES
--   1. Deletes duplicate (playlist_id, song_id) rows from public.playlist_songs,
--      keeping the copy added first, then closes the position gaps. This is the
--      only destructive step. It is required before the unique constraint below
--      can be added.
--   2. Adds UNIQUE (playlist_id, song_id) as uq_playlist_song, matching
--      app/models/playlist.py.
--   3. Enables Row Level Security on every table in `public` and revokes the
--      standing grants to the `anon` and `authenticated` roles.
--
-- WHY STEP 3 IS DENY-ALL RATHER THAN USER-SCOPED POLICIES
--   Supabase RLS policies identify the caller via auth.uid(), which reads a
--   Supabase-issued JWT. This project authenticates with Firebase
--   (app/config/firebase.py), so no such JWT exists and auth.uid() would be NULL
--   on every request. User-scoped policies are therefore not possible here
--   without a Firebase->Supabase token bridge. Authorization already lives in
--   app/middleware/firebase_auth.py, and the backend connects over the Postgres
--   protocol as the table owner, which bypasses RLS. So the correct posture is:
--   deny every web-facing role at the database edge, keep authz in the app.
--
--   RLS with no policy attached is the load-bearing control -- it denies all rows
--   to every non-bypassing role regardless of what GRANTs exist. The REVOKEs are
--   hygiene layered on top.
--
-- Read-only version of the checks, if you ever want them separately: audit_rls.sql

BEGIN;

-- ===========================================================================
-- PART 0 -- Safety guard. Aborts the transaction if this migration would break
--           the backend. Nothing below runs unless these checks pass.
-- ===========================================================================
DO $guard$
DECLARE
    v_backend_role CONSTANT text := 'postgres';  -- role behind the pooler user postgres.<project-ref>
    v_bypass       boolean;
    v_foreign      text;
    v_forced       text;
BEGIN
    SELECT rolbypassrls OR rolsuper
      INTO v_bypass
      FROM pg_roles
     WHERE rolname = v_backend_role;

    IF v_bypass IS NULL THEN
        RAISE EXCEPTION
            'ABORTED: expected a role named % to exist (the role the backend connects as). '
            'This does not look like a Supabase database. Nothing was changed.',
            v_backend_role;
    END IF;

    -- Tables the backend neither owns nor can reach past RLS.
    SELECT string_agg(c.relname, ', ' ORDER BY c.relname)
      INTO v_foreign
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relkind = 'r'
       AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = v_backend_role);

    IF NOT v_bypass AND v_foreign IS NOT NULL THEN
        RAISE EXCEPTION
            'ABORTED: role % has neither rolbypassrls nor ownership of: %. '
            'Enabling RLS would deny the backend access to those tables. '
            'Nothing was changed. Run audit_rls.sql and check the owner column.',
            v_backend_role, v_foreign;
    END IF;

    -- FORCE ROW LEVEL SECURITY makes even the owner obey policies.
    SELECT string_agg(c.relname, ', ' ORDER BY c.relname)
      INTO v_forced
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public'
       AND c.relkind = 'r'
       AND c.relforcerowsecurity;

    IF v_forced IS NOT NULL AND NOT v_bypass THEN
        RAISE EXCEPTION
            'ABORTED: FORCE ROW LEVEL SECURITY is set on: %. The owner would be '
            'subject to policies too, and there are none. Nothing was changed.',
            v_forced;
    END IF;

    RAISE NOTICE 'Safety check passed: role % bypasses RLS (rolbypassrls/rolsuper = %).',
        v_backend_role, v_bypass;
END
$guard$;


-- ===========================================================================
-- PART 1 -- public.playlist_songs: dedupe, recompact positions, add the
--           unique constraint. Skipped cleanly if the table does not exist.
-- ===========================================================================
DO $migrate$
DECLARE
    v_removed  integer := 0;
    v_shifted  integer := 0;
BEGIN
    IF to_regclass('public.playlist_songs') IS NULL THEN
        RAISE NOTICE 'public.playlist_songs does not exist -- skipping PART 1.';
        RETURN;
    END IF;

    -- Collapse duplicates, keeping the copy that was added first.
    WITH ranked AS (
        SELECT id,
               row_number() OVER (
                   PARTITION BY playlist_id, song_id
                   ORDER BY added_at, position, id
               ) AS rn
        FROM public.playlist_songs
    )
    DELETE FROM public.playlist_songs ps
     USING ranked r
     WHERE ps.id = r.id
       AND r.rn > 1;
    GET DIAGNOSTICS v_removed = ROW_COUNT;

    -- Close the gaps that leaves. The service layer assumes positions are
    -- contiguous and zero-based within each playlist.
    WITH renumbered AS (
        SELECT id,
               (row_number() OVER (
                   PARTITION BY playlist_id
                   ORDER BY position, added_at, id
               ))::int - 1 AS new_position
        FROM public.playlist_songs
    )
    UPDATE public.playlist_songs AS ps
       SET position = r.new_position
      FROM renumbered AS r
     WHERE ps.id = r.id
       AND ps.position <> r.new_position;
    GET DIAGNOSTICS v_shifted = ROW_COUNT;

    RAISE NOTICE 'playlist_songs: % duplicate row(s) deleted, % position(s) renumbered.',
        v_removed, v_shifted;

    -- DROP first so a re-run is a no-op rather than an error.
    EXECUTE 'ALTER TABLE public.playlist_songs DROP CONSTRAINT IF EXISTS uq_playlist_song';
    EXECUTE 'ALTER TABLE public.playlist_songs ADD CONSTRAINT uq_playlist_song UNIQUE (playlist_id, song_id)';
    RAISE NOTICE 'playlist_songs: uq_playlist_song applied.';
END
$migrate$;


-- ===========================================================================
-- PART 2 -- Enable RLS on every table in `public`, and revoke the standing
--           grants to the web-facing roles.
-- ===========================================================================
DO $lockdown$
DECLARE
    t          text;
    v_enabled  integer := 0;
    v_role     text;
    v_roles    text[] := ARRAY['anon', 'authenticated'];
BEGIN
    FOR t IN
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind = 'r'
           AND NOT c.relrowsecurity
         ORDER BY c.relname
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        v_enabled := v_enabled + 1;
        RAISE NOTICE 'RLS enabled on public.%', t;
    END LOOP;

    IF v_enabled = 0 THEN
        RAISE NOTICE 'RLS was already enabled on every table in public -- nothing to do.';
    END IF;

    -- Revoke only for roles that actually exist, so this also runs on a plain
    -- Postgres instance without the Supabase roles.
    FOREACH v_role IN ARRAY v_roles LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_role) THEN
            RAISE NOTICE 'Role % does not exist -- skipping its REVOKEs.', v_role;
            CONTINUE;
        END IF;

        EXECUTE format('REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM %I', v_role);
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', v_role);
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM %I', v_role);

        -- Stop tables created later from inheriting the grants back. This only
        -- edits defaults recorded for the role running it; PART 3 reports any
        -- entries owned by a different grantor, which would need FOR ROLE.
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM %I', v_role);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', v_role);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I', v_role);

        RAISE NOTICE 'Revoked table/sequence/function privileges from % (incl. future objects).', v_role;
    END LOOP;
END
$lockdown$;

COMMIT;


-- ===========================================================================
-- PART 3 -- Verification. Every row of the first result should read
--           rls_enabled = t, anon_can_select = f, anon_can_insert = f.
-- ===========================================================================
SELECT c.relname                                     AS table_name,
       c.relrowsecurity                              AS rls_enabled,
       has_table_privilege('anon', c.oid, 'SELECT')   AS anon_can_select,
       has_table_privilege('anon', c.oid, 'INSERT')   AS anon_can_insert,
       has_table_privilege('anon', c.oid, 'UPDATE')   AS anon_can_update,
       has_table_privilege('anon', c.oid, 'DELETE')   AS anon_can_delete
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
ORDER BY c.relname;

-- Expect exactly one row: uq_playlist_song | UNIQUE (playlist_id, song_id)
-- to_regclass returns NULL rather than raising if the table is absent, so this
-- yields no rows instead of an error on a database without playlist_songs.
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = to_regclass('public.playlist_songs')
  AND contype = 'u';

-- Any row here granted by a role other than the one you ran this as still
-- applies to newly created tables and needs its own ALTER DEFAULT PRIVILEGES
-- ... FOR ROLE <grantor> statement. PART 2's RLS loop still protects such
-- tables, because create_all() never creates policies.
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
WHERE n.nspname = 'public' OR n.nspname IS NULL
ORDER BY granted_by, schema, object_type;


-- ===========================================================================
-- ROLLBACK, only if the backend turns out to be affected.
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
-- Restoring those grants puts the exposure back, so prefer diagnosing over
-- rolling back. PART 0 should have caught the lockout case before it happened;
-- if the backend still breaks, the cause is that it connects as a role which
-- neither owns the tables nor has rolbypassrls -- check the DATABASE_URL user.
-- ===========================================================================
