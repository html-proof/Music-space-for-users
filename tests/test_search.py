"""
Search across tracks, albums and artists.

Gaana is stubbed in every test: the point is our own mapping and caching, not
Gaana's availability.

There is no local-catalogue fallback to test any more. Search retrieval is
Gaana and only Gaana -- when it is unreachable the endpoint returns an empty
success rather than serving whatever rows happen to be in `songs`/`albums`,
which used to make results a view of our own ingest.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Album, Artist, Song
from app.services.catalog_service import catalog_service

NO_RESULTS = {"error": "no results found"}

GAANA_ALBUM = {
    "seokey": "discovery",
    "album_id": "1234",
    "title": "Discovery",
    "artists": "Daft Punk, Guest",
    "artist_seokeys": "daft-punk",
    "artist_ids": "9001",
    "duration": "3600",
    "is_explicit": False,
    "language": "English",
    "label": "Virgin",
    "track_count": "14",
    "release_date": "2001-03-12",
    "images": {"urls": {"large_artwork": "https://img/large.jpg"}},
}

GAANA_SONG = {
    "seokey": "one-more-time",
    "track_id": "5678",
    "title": "One More Time",
    "artists": "Daft Punk",
    "artist_seokeys": "daft-punk",
    "artist_ids": "9001",
    "album": "Discovery",
    "album_seokey": "discovery",
    "album_id": "1234",
    "duration": "320",
    "language": "English",
    "genres": "Electronic",
    "is_explicit": False,
    "images": {"urls": {"large_artwork": "https://img/large.jpg"}},
    "stream_urls": {"urls": {"high_quality": "https://stream/hq.mp4"}},
}

GAANA_ARTIST = {
    "seokey": "daft-punk",
    "artist_id": "9001",
    "name": "Daft Punk",
    "song_count": "212",
    "album_count": "17",
    "images": {"urls": {"large_artwork": "https://img/artist-large.jpg"}},
}


@pytest.fixture
def stub_upstream(monkeypatch):
    """Return canned upstream payloads for each of the three search kinds."""
    async def albums(*args, **kwargs):
        return [GAANA_ALBUM]

    async def artists(*args, **kwargs):
        return [GAANA_ARTIST]

    async def songs(*args, **kwargs):
        return [GAANA_SONG]

    monkeypatch.setattr(catalog_service.gaana, "search_albums", albums)
    monkeypatch.setattr(catalog_service.gaana, "search_artists", artists)
    monkeypatch.setattr(catalog_service.gaana, "search_songs", songs)


@pytest.fixture
def upstream_down(monkeypatch):
    """Every search entry point fails, as it does when Gaana is unreachable."""
    async def failed(*args, **kwargs):
        return NO_RESULTS

    for method in ("search_songs", "search_albums", "search_artists"):
        monkeypatch.setattr(catalog_service.gaana, method, failed)


async def seed_local(db: AsyncSession):
    """Rows that match the query but never came from this request Gaana call.

    Used to assert they are *not* returned: they stand in for a previously
    ingested catalog, which search must no longer read from.
    """
    artist = Artist(external_id="local-artist", name="Local Legend", seokey="local-legend")
    db.add(artist)
    await db.commit()
    await db.refresh(artist)

    album = Album(
        external_id="local-album",
        title="Local Legend Live",
        seokey="local-legend-live",
        artist_id=artist.id,
        artist_name="Local Legend",
        track_count=9,
    )
    song = Song(
        external_id="local-song",
        title="Local Legend Anthem",
        artist_name="Local Legend",
        duration=200,
    )
    db.add_all([album, song])
    await db.commit()
    await db.refresh(album)
    await db.refresh(song)
    return artist, album, song


# --------------------------------------------------------------------------
# Response shape
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_all_three_kinds(
    client: AsyncClient, auth_headers: dict, stub_upstream
):
    res = await client.get("/api/search?query=Daft+Punk", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    assert data["type"] == "all"
    assert {"query", "songs", "albums", "artists", "total"} <= set(data)
    assert data["albums"][0]["title"] == "Discovery"
    assert data["artists"][0]["name"] == "Daft Punk"


@pytest.mark.asyncio
async def test_songs_key_is_preserved_for_existing_clients(
    client: AsyncClient, auth_headers: dict, stub_upstream
):
    """Clients read `songs`; renaming it to `tracks` would break them."""
    res = await client.get("/api/search?query=Daft+Punk", headers=auth_headers)
    assert "songs" in res.json()["data"]
    assert "tracks" not in res.json()["data"]


@pytest.mark.asyncio
async def test_album_fields_are_mapped_from_upstream(
    client: AsyncClient, auth_headers: dict, stub_upstream
):
    res = await client.get("/api/search?query=Discovery&type=album", headers=auth_headers)
    album = res.json()["data"]["albums"][0]

    assert album["external_id"] == "1234"
    assert album["seokey"] == "discovery"
    assert album["artist_name"] == "Daft Punk, Guest"
    assert album["cover_url"] == "https://img/large.jpg"
    assert album["language"] == "English"
    assert album["release_date"] == "2001-03-12"
    # Arrives as a string upstream and must land on an Integer column.
    assert album["track_count"] == 14


@pytest.mark.asyncio
async def test_artist_fields_are_mapped_from_upstream(
    client: AsyncClient, auth_headers: dict, stub_upstream
):
    res = await client.get("/api/search?query=Daft&type=artist", headers=auth_headers)
    artist = res.json()["data"]["artists"][0]

    assert artist["external_id"] == "9001"
    assert artist["seokey"] == "daft-punk"
    assert artist["image_url"] == "https://img/artist-large.jpg"
    assert artist["song_count"] == 212
    assert artist["album_count"] == 17


@pytest.mark.asyncio
async def test_album_search_creates_the_credited_artist(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_upstream
):
    await client.get("/api/search?query=Discovery&type=album", headers=auth_headers)

    res = await db_session.execute(select(Artist).where(Artist.name == "Daft Punk"))
    artist = res.scalars().first()
    assert artist is not None, "the album's artist should have been created"

    res = await db_session.execute(select(Album).where(Album.external_id == "1234"))
    album = res.scalars().first()
    assert album is not None
    assert album.artist_id == artist.id


@pytest.mark.asyncio
async def test_album_search_results_are_committed(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_upstream
):
    """
    get_or_create_album only flushes. Without an explicit commit the rows would
    vanish at the end of the request and every search would re-fetch.
    """
    first = await client.get("/api/search?query=Discovery&type=album", headers=auth_headers)
    album_id = first.json()["data"]["albums"][0]["id"]

    res = await db_session.execute(select(Album).where(Album.id == album_id))
    assert res.scalars().first() is not None


# --------------------------------------------------------------------------
# The type filter
# --------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "search_type,populated",
    [("track", "songs"), ("album", "albums"), ("artist", "artists")],
)
async def test_type_filter_only_populates_its_own_kind(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession,
    stub_upstream, search_type, populated
):
    await seed_local(db_session)

    res = await client.get(
        f"/api/search?query=Local+Legend&type={search_type}", headers=auth_headers
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    assert data["type"] == search_type
    assert data[populated], f"{search_type} should have populated {populated}"
    for other in ({"songs", "albums", "artists"} - {populated}):
        assert data[other] == [], f"{search_type} should not have queried {other}"


@pytest.mark.asyncio
async def test_unknown_type_is_rejected(client: AsyncClient, auth_headers: dict):
    res = await client.get("/api/search?query=x&type=podcast", headers=auth_headers)
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_type_is_recorded_on_the_search_history_row(
    client: AsyncClient, auth_headers: dict, stub_upstream
):
    await client.get("/api/search?query=Discovery&type=album", headers=auth_headers)

    res = await client.get("/api/search/history", headers=auth_headers)
    assert res.json()["data"][0]["result_type"] == "album"


# --------------------------------------------------------------------------
# Degraded upstream
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unreachable_gaana_is_an_empty_success_not_local_rows(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, upstream_down
):
    """When Gaana is down, search returns nothing -- it must not fall back to
    the database.

    Rows matching this exact query are seeded first, so the assertion is
    specifically that they are ignored. Serving them would make search a view
    of whatever had previously been ingested rather than of Gaana, and would do
    it silently: the client cannot tell a degraded result from a real one.
    """
    await seed_local(db_session)

    res = await client.get("/api/search?query=Local+Legend", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]

    assert data["songs"] == []
    assert data["albums"] == []
    assert data["artists"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_local_rows_never_widen_a_successful_gaana_search(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_upstream
):
    """A song already in the database that matches the query, but that Gaana did
    not return, must not be merged into the results.

    Local lexical matches used to be added to the ranking pool "for recall".
    That quietly reintroduced the database as a second source: two users
    searching the same term got different results depending on what each
    deployment had ingested.
    """
    await seed_local(db_session)

    res = await client.get("/api/search?query=Local+Legend&type=track", headers=auth_headers)
    assert res.status_code == 200, res.text
    titles = [s["title"] for s in res.json()["data"]["songs"]]
    assert titles == ["One More Time"]  # only what the stub returned
    assert "Local Legend Anthem" not in titles


@pytest.mark.asyncio
async def test_no_matches_anywhere_is_an_empty_success(
    client: AsyncClient, auth_headers: dict, upstream_down
):
    res = await client.get("/api/search?query=zzzznothing", headers=auth_headers)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["songs"] == data["albums"] == data["artists"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_search_works_unauthenticated(client: AsyncClient, stub_upstream):
    """Search is public; only the history side effect needs a user."""
    res = await client.get("/api/search?query=Daft+Punk")
    assert res.status_code == 200, res.text
    assert res.json()["data"]["artists"][0]["name"] == "Daft Punk"


@pytest.mark.asyncio
async def test_limit_is_bounded(client: AsyncClient, auth_headers: dict):
    assert (await client.get("/api/search?query=x&limit=0", headers=auth_headers)).status_code == 422
    assert (await client.get("/api/search?query=x&limit=51", headers=auth_headers)).status_code == 422


@pytest.mark.asyncio
async def test_blank_query_is_rejected(client: AsyncClient, auth_headers: dict):
    assert (await client.get("/api/search?query=", headers=auth_headers)).status_code == 422


# --------------------------------------------------------------------------
# Deduplication against song-created rows
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_album_search_reuses_a_row_created_while_upserting_a_song(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_upstream
):
    """
    Songs create their album with Gaana's album_id as external_id. Album search
    keys off the same field, so it must find that row instead of duplicating it.
    """
    existing = await catalog_service.get_or_create_album(
        db_session,
        title="Discovery",
        external_id="1234",
        artist_name="Daft Punk, Guest",
    )
    await db_session.commit()
    existing_id = existing.id

    res = await client.get("/api/search?query=Discovery&type=album", headers=auth_headers)
    assert res.json()["data"]["albums"][0]["id"] == existing_id

    rows = await db_session.execute(select(Album).where(Album.external_id == "1234"))
    assert len(rows.scalars().all()) == 1


@pytest.mark.asyncio
async def test_album_search_enriches_a_sparse_existing_row(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_upstream
):
    """A song-created album has no track count or release date; search fills them."""
    await catalog_service.get_or_create_album(
        db_session, title="Discovery", external_id="1234", artist_name="Daft Punk, Guest"
    )
    await db_session.commit()

    res = await client.get("/api/search?query=Discovery&type=album", headers=auth_headers)
    album = res.json()["data"]["albums"][0]
    assert album["track_count"] == 14
    assert album["release_date"] == "2001-03-12"
    assert album["language"] == "English"


@pytest.mark.asyncio
async def test_malformed_upstream_entries_are_skipped(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """
    Individual entries come back as error dicts when Gaana cannot format them.
    Those must be dropped, not turned into rows titled "Unknown".
    """
    async def mixed_albums(*args, **kwargs):
        return [{"error": "invalid seokey"}, GAANA_ALBUM, {"not": "an album"}]

    async def mixed_artists(*args, **kwargs):
        return [{"error": "invalid seokey"}, GAANA_ARTIST]

    monkeypatch.setattr(catalog_service.gaana, "search_albums", mixed_albums)
    monkeypatch.setattr(catalog_service.gaana, "search_artists", mixed_artists)

    albums = await client.get("/api/search?query=x&type=album", headers=auth_headers)
    assert [a["title"] for a in albums.json()["data"]["albums"]] == ["Discovery"]

    artists = await client.get("/api/search?query=x&type=artist", headers=auth_headers)
    assert [a["name"] for a in artists.json()["data"]["artists"]] == ["Daft Punk"]


@pytest.mark.asyncio
async def test_non_numeric_counts_do_not_error(
    client: AsyncClient, auth_headers: dict, monkeypatch
):
    """Upstream counts are strings and are sometimes empty."""
    async def sloppy_albums(*args, **kwargs):
        return [{**GAANA_ALBUM, "track_count": ""}]

    async def sloppy_artists(*args, **kwargs):
        return [{**GAANA_ARTIST, "song_count": "", "album_count": "lots"}]

    monkeypatch.setattr(catalog_service.gaana, "search_albums", sloppy_albums)
    monkeypatch.setattr(catalog_service.gaana, "search_artists", sloppy_artists)

    albums = await client.get("/api/search?query=x&type=album", headers=auth_headers)
    assert albums.status_code == 200, albums.text
    assert albums.json()["data"]["albums"][0]["track_count"] == 0

    artists = await client.get("/api/search?query=x&type=artist", headers=auth_headers)
    assert artists.status_code == 200, artists.text
    assert artists.json()["data"]["artists"][0]["song_count"] == 0
