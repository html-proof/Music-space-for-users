"""The write path for Gaana tracks: correctness, and its cost.

Cost is tested here because it is the thing that actually broke. Fetching a
whole home feed from Gaana is only viable if writing the rows back is cheap, and
the per-song version was not: three SELECTs, a COMMIT and a REFRESH for every
track, which came to ~500 statements for one cold feed. Against SQLite that is
invisible; against a managed Postgres in another region it is tens of seconds,
and it is what put the home screen past the client's 45s timeout.

So `test_upsert_cost_does_not_scale_with_batch_size` counts statements rather
than measuring time -- statements are round trips, and round trips are the cost
that does not show up on a developer machine.
"""
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Album, Artist, Song
from app.services.catalog_upsert import parse_track, upsert_tracks


def raw_track(i, *, artist="Batch Artist", album_id="album-1", language="Malayalam"):
    return {
        "seokey": f"batch-{i}",
        "track_id": f"batch-{i}",
        "title": f"Batch Track {i}",
        "artists": f"{artist}, Guest {i}",
        "artist_seokeys": "batch-artist,guest",
        "artist_ids": "555,556",
        "album": "Batch Album",
        "album_seokey": "batch-album",
        "album_id": album_id,
        "duration": "200",
        "language": language,
        "genres": "Pop",
        "is_explicit": False,
        "images": {"urls": {"large_artwork": "https://img/l.jpg"}},
        "stream_urls": {"urls": {"high_quality": "https://s/hq.mp4"}},
    }


class StatementCounter:
    """Counts SQL statements issued on a session's engine."""

    def __init__(self, db: AsyncSession):
        # get_bind() hands back the sync Engine that the async one wraps, which
        # is where the DBAPI-level events actually fire.
        self.engine = db.get_bind()
        self.count = 0

    def _on_execute(self, conn, cursor, statement, params, context, executemany):
        self.count += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._on_execute)


@pytest.mark.asyncio
async def test_upsert_creates_songs_artists_and_albums(db_session: AsyncSession):
    songs = await upsert_tracks(db_session, [raw_track(i) for i in range(3)])

    assert [s.title for s in songs] == ["Batch Track 0", "Batch Track 1", "Batch Track 2"]
    # The full credit string is kept on the song; the artist row is the first
    # credited name, which is what "more of this artist" means.
    assert songs[0].artist_name.startswith("Batch Artist")
    artist = (
        await db_session.execute(select(Artist).where(Artist.external_id == "555"))
    ).scalar_one()
    assert artist.name == "Batch Artist"
    assert all(s.artist_id == artist.id for s in songs)

    album = (
        await db_session.execute(select(Album).where(Album.external_id == "album-1"))
    ).scalar_one()
    assert album.artist_id == artist.id
    assert all(s.album_id == album.id for s in songs)


@pytest.mark.asyncio
async def test_upsert_is_idempotent_and_deduplicates_within_a_batch(db_session: AsyncSession):
    """The same track twice in one batch, and again in a later one, is one row.

    This is what lets several shelves fetch overlapping tracks without the
    catalog growing a copy per shelf.
    """
    first = await upsert_tracks(db_session, [raw_track(1), raw_track(1), raw_track(2)])
    assert len(first) == 2

    second = await upsert_tracks(db_session, [raw_track(1), raw_track(3)])
    assert {s.external_id for s in second} == {"batch-1", "batch-3"}

    assert await db_session.scalar(select(func.count()).select_from(Song)) == 3
    assert await db_session.scalar(select(func.count()).select_from(Artist)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Album)) == 1
    # The re-upserted track kept its identity rather than being replaced.
    assert first[0].id == second[0].id


@pytest.mark.asyncio
async def test_a_sparse_payload_never_blanks_a_richer_one(db_session: AsyncSession):
    """Gaana responses vary in completeness; a thin one must not erase data.

    Search results in particular often omit stream urls that a detail fetch
    supplied earlier -- overwriting unconditionally would make a track
    unplayable purely because it turned up in a search.
    """
    rich = raw_track(9)
    await upsert_tracks(db_session, [rich])

    sparse = dict(rich)
    sparse["stream_urls"] = {"urls": {}}
    sparse["images"] = {"urls": {}}
    sparse["language"] = ""
    song = (await upsert_tracks(db_session, [sparse]))[0]

    assert song.audio_url == "https://s/hq.mp4"
    assert song.thumbnail_url == "https://img/l.jpg"
    assert song.language == "Malayalam"


@pytest.mark.asyncio
async def test_singles_without_an_album_id_do_not_collapse_together(db_session: AsyncSession):
    """Gaana sends no album id for many singles, and they all carry the literal
    title "Single". Matching on that title would merge unrelated singles from
    every artist into one album row."""
    raws = []
    for i in (1, 2):
        raw = raw_track(i, artist=f"Solo {i}")
        raw["album"] = "Single"
        raw["album_id"] = None
        raw["album_seokey"] = None
        raws.append(raw)

    songs = await upsert_tracks(db_session, raws)
    assert songs[0].album_id != songs[1].album_id
    assert await db_session.scalar(select(func.count()).select_from(Album)) == 2


@pytest.mark.asyncio
async def test_unlabelled_metadata_is_left_empty_rather_than_invented(db_session: AsyncSession):
    """A track Gaana gives no language/genre/mood for gets none.

    The per-song path defaulted these to "English"/"Pop"/"Chill", which is
    fabricated catalog metadata -- and it fed straight back into recommendation
    filtering, so a track with no stated language became an English track.
    """
    raw = raw_track(4)
    raw["language"] = ""
    raw["genres"] = ""
    raw.pop("mood", None)

    song = (await upsert_tracks(db_session, [raw]))[0]
    assert song.language == ""
    assert song.genre == ""
    assert song.mood == ""


@pytest.mark.asyncio
async def test_malformed_entries_are_skipped(db_session: AsyncSession):
    """Gaana returns per-entry error dicts when it cannot format a track."""
    assert parse_track({"error": "invalid seokey"}) is None
    assert parse_track({"title": "no key at all"}) is None
    assert parse_track("not a dict") is None

    songs = await upsert_tracks(
        db_session, [{"error": "bad"}, raw_track(7), {"nope": True}]
    )
    assert [s.external_id for s in songs] == ["batch-7"]


@pytest.mark.asyncio
async def test_upsert_cost_does_not_scale_with_batch_size(db_session: AsyncSession):
    """Round trips must stay flat as the batch grows.

    The regression this guards is the one that broke the home screen: writing
    per song meant ~5 statements *each*, so a feed pulling a couple of hundred
    tracks spent tens of seconds in database latency alone. Forty tracks must
    cost about what four do.
    """
    with StatementCounter(db_session) as small:
        await upsert_tracks(db_session, [raw_track(i) for i in range(4)])

    with StatementCounter(db_session) as large:
        await upsert_tracks(
            db_session,
            [raw_track(i, album_id=f"album-{i % 6}") for i in range(100, 140)],
        )

    assert small.count <= 8, f"even a small batch should be a handful: {small.count}"
    # Ten times the tracks must not cost ten times the round trips. A little
    # growth is fine (more rows per INSERT batch); proportional growth is the bug.
    assert large.count <= small.count + 6, (
        f"upsert cost is scaling with batch size: {small.count} -> {large.count}"
    )
