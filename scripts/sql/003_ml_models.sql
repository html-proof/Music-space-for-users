-- 003_ml_models.sql
--
-- Creates the `ml_models` table from app/models/ml.py on an EXISTING Supabase
-- database.
--
-- Why this is manual: app/db/init_db.py calls Base.metadata.create_all with the
-- default checkfirst=True. That *will* create a brand-new table on the next boot,
-- so on a database that is already live this migration is only strictly needed
-- when init_db is not run (a read-only role, or a deploy that skips it). It is
-- kept for two other reasons: it documents the exact shape the application
-- expects, and it grants nothing to `anon`, which create_all would leave to the
-- schema-wide defaults.
--
-- Safe to run more than once: every statement is IF NOT EXISTS.
--
-- Run in Supabase Dashboard > SQL Editor.

-- ---------------------------------------------------------------------------
-- 1. The table.
--
--    `artifact` and `metrics` are jsonb, matching UniversalJSON's Postgres
--    branch (app/db/base.py). Artifacts are small by design -- the ranker is
--    ~25 floats, item_sim is top-K neighbours for interacted songs only -- so no
--    TOAST tuning or separate blob store is warranted.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.ml_models (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(50) NOT NULL,
    version     integer     NOT NULL DEFAULT 1,
    artifact    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    metrics     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    n_samples   integer     NOT NULL DEFAULT 0,
    n_users     integer     NOT NULL DEFAULT 0,
    is_active   boolean     NOT NULL DEFAULT false,
    notes       varchar(1024),
    trained_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 2. Indexes.
--
--    (name, is_active) serves the load-the-active-model query, which runs on
--    every cache miss -- i.e. once a minute per web process per model.
--    (name, version) is UNIQUE: registry.next_version() reads MAX(version) and
--    adds one, so two concurrent training passes could otherwise both write
--    version N. The constraint makes the loser fail loudly instead of leaving
--    two rows claiming the same version.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_ml_models_name
    ON public.ml_models (name);

CREATE INDEX IF NOT EXISTS ix_ml_models_is_active
    ON public.ml_models (is_active);

CREATE INDEX IF NOT EXISTS ix_ml_models_trained_at
    ON public.ml_models (trained_at);

CREATE INDEX IF NOT EXISTS ix_ml_models_name_active
    ON public.ml_models (name, is_active);

CREATE UNIQUE INDEX IF NOT EXISTS ix_ml_models_name_version
    ON public.ml_models (name, version);

-- ---------------------------------------------------------------------------
-- 3. Lock it down.
--
--    The backend connects as the table owner and is the only writer: models are
--    written by scripts/train_ml.py, the in-process trainer, or
--    POST /api/ml/retrain. Nothing reaches this table through the Supabase REST
--    API, so `anon` and `authenticated` need no privileges at all -- and a
--    client that could UPDATE `artifact` could set the ranking weights every
--    user's feed is scored with.
--
--    This mirrors scripts/sql/002_lock_down_public_schema.sql. If 002 has not
--    been applied yet, the schema-wide GRANTs it revokes still apply to this new
--    table, so run 002 as well.
-- ---------------------------------------------------------------------------
ALTER TABLE public.ml_models ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.ml_models FROM anon, authenticated;

-- No policies are created on purpose. With RLS enabled and no policy, the
-- non-owner roles can read and write nothing; the owner bypasses RLS.

-- ---------------------------------------------------------------------------
-- 4. Verify. Expect: rls = true, policies = 0, anon_sel = false.
-- ---------------------------------------------------------------------------
SELECT c.relname                                                   AS table_name,
       c.relrowsecurity                                             AS rls,
       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)  AS policies,
       has_table_privilege('anon', c.oid, 'SELECT')                 AS anon_sel,
       has_table_privilege('anon', c.oid, 'UPDATE')                 AS anon_upd
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname = 'ml_models';

SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename = 'ml_models'
ORDER BY indexname;
