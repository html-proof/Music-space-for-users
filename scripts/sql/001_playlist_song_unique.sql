-- 001_playlist_song_unique.sql
--
-- Adds UniqueConstraint("playlist_id", "song_id", name="uq_playlist_song") from
-- app/models/playlist.py to an EXISTING Supabase database.
--
-- Why this is manual: app/db/init_db.py calls Base.metadata.create_all, which
-- defaults to checkfirst=True and therefore skips any table that already exists.
-- It creates tables; it never alters them. The constraint would only appear on a
-- database built from scratch, so the live table needs it applied directly.
--
-- Run in Supabase Dashboard > SQL Editor. Steps 1-3 must run before step 4:
-- ADD CONSTRAINT fails outright if any duplicate pair already exists.

-- ---------------------------------------------------------------------------
-- 1. Inspect. Read-only -- tells you whether step 2 has anything to do.
-- ---------------------------------------------------------------------------
SELECT playlist_id, song_id, count(*) AS copies
FROM public.playlist_songs
GROUP BY playlist_id, song_id
HAVING count(*) > 1
ORDER BY copies DESC, playlist_id;

-- ---------------------------------------------------------------------------
-- 2. Collapse duplicates, keeping the copy that was added first.
-- ---------------------------------------------------------------------------
DELETE FROM public.playlist_songs
WHERE id IN (
    SELECT id
    FROM (
        SELECT id,
               row_number() OVER (
                   PARTITION BY playlist_id, song_id
                   ORDER BY added_at, position, id
               ) AS rn
        FROM public.playlist_songs
    ) ranked
    WHERE rn > 1
);

-- ---------------------------------------------------------------------------
-- 3. Close the position gaps step 2 left behind. The service layer assumes
--    positions are contiguous and zero-based within a playlist.
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 4. Add the constraint. DROP IF EXISTS first so re-running is safe.
--
--    This takes an ACCESS EXCLUSIVE lock while it builds the backing index --
--    instant on a small table. For a large one, build the index without
--    blocking writes instead:
--      CREATE UNIQUE INDEX CONCURRENTLY uq_playlist_song
--          ON public.playlist_songs (playlist_id, song_id);
--      ALTER TABLE public.playlist_songs
--          ADD CONSTRAINT uq_playlist_song UNIQUE USING INDEX uq_playlist_song;
--    (CONCURRENTLY cannot run inside a transaction block.)
-- ---------------------------------------------------------------------------
ALTER TABLE public.playlist_songs
    DROP CONSTRAINT IF EXISTS uq_playlist_song;

ALTER TABLE public.playlist_songs
    ADD CONSTRAINT uq_playlist_song UNIQUE (playlist_id, song_id);

-- ---------------------------------------------------------------------------
-- 5. Verify. Expect one row: uq_playlist_song | UNIQUE (playlist_id, song_id)
-- ---------------------------------------------------------------------------
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'public.playlist_songs'::regclass
  AND contype = 'u';
