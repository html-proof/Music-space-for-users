"""Opening an album from a song.

A song payload carries `album_id` -- our uuid -- and nothing else about its
album. Gaana album lookups are keyed by seokey. So "open the album this song is
on" was impossible from every screen that lists songs: the client held the wrong
kind of identifier and had no way to trade it for the right one.

`/api/catalog/albums/info` now resolves whichever identifier a client happens to
hold. The parameter keeps its name for compatibility.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.song import Album
from app.services.catalog_service import catalog_service

GAANA_ALBUM_PAYLOAD = {
    "seokey": "manjummel-boys",
    "album_id": "8879668",
    "title": "Manjummel Boys",
    "artists": "Sushin Shyam",
    "language": "Malayalam",
    "track_count": "6",
    "release_date": "2024-02-22",
    "images": {"urls": {"large_artwork": "https://img/large.jpg"}},
    "tracks": [],
}


@pytest.fixture
def stub_album(monkeypatch):
    """Gaana album detail, plus a record of what seokey it was asked for."""
    asked = []

    async def get_album_info(seokeys, *args, **kwargs):
        asked.append(seokeys[0] if seokeys else None)
        if seokeys and seokeys[0] == "manjummel-boys":
            return [GAANA_ALBUM_PAYLOAD]
        return {"error": "no results found"}

    monkeypatch.setattr(catalog_service.gaana, "get_album_info", get_album_info)
    return asked


async def seed_album(db: AsyncSession, **overrides) -> Album:
    album = Album(
        external_id=overrides.get("external_id", "8879668"),
        title="Manjummel Boys",
        seokey=overrides.get("seokey", "manjummel-boys"),
        artist_name="Sushin Shyam",
    )
    db.add(album)
    await db.commit()
    await db.refresh(album)
    return album


@pytest.mark.asyncio
async def test_album_opens_by_our_uuid(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_album
):
    """The identifier a song payload actually carries.

    This is the whole point: `song.album_id` is a uuid, and before this it could
    not open anything.
    """
    album = await seed_album(db_session)

    res = await client.get(
        f"/api/catalog/albums/info?seokey={album.id}", headers=auth_headers
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"][0]["title"] == "Manjummel Boys"
    assert stub_album == ["manjummel-boys"], "should have resolved to the Gaana seokey"


@pytest.mark.asyncio
async def test_album_opens_by_gaana_numeric_id(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_album
):
    """Gaana numeric album_id, which we store as external_id."""
    await seed_album(db_session)

    res = await client.get("/api/catalog/albums/info?seokey=8879668", headers=auth_headers)
    assert res.status_code == 200, res.text
    assert stub_album == ["manjummel-boys"]


@pytest.mark.asyncio
async def test_a_seokey_still_passes_straight_through(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_album
):
    """The original contract. No database row is needed for this path, which is
    what keeps album detail working for an album nobody has ever touched."""
    res = await client.get(
        "/api/catalog/albums/info?seokey=manjummel-boys", headers=auth_headers
    )
    assert res.status_code == 200, res.text
    assert stub_album == ["manjummel-boys"]


@pytest.mark.asyncio
async def test_a_single_has_no_album_page(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_album
):
    """Gaana names no album for many singles, so `catalog_upsert` mints a
    synthetic `single-<track seokey>` row. There is no Gaana album behind it, so
    the request must 404 rather than pass our own invented key upstream."""
    album = await seed_album(db_session, external_id="single-some-track", seokey=None)

    res = await client.get(
        f"/api/catalog/albums/info?seokey={album.id}", headers=auth_headers
    )
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert stub_album == [], "must not call Gaana with an identifier it cannot use"


@pytest.mark.asyncio
async def test_an_unknown_uuid_is_a_clean_404(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, stub_album
):
    res = await client.get(
        "/api/catalog/albums/info?seokey=3f2504e0-4f89-11d3-9a0c-0305e82c3301",
        headers=auth_headers,
    )
    assert res.status_code == 404, res.text
    assert stub_album == []


@pytest.mark.asyncio
async def test_resolver_prefers_a_real_seokey_over_a_lookup(db_session: AsyncSession):
    """A seokey resolves without touching the database at all.

    Album detail has to keep working for albums we have never ingested, so a
    value that is already a seokey must never depend on a local row existing.
    """
    assert await catalog_service.resolve_album_seokey(db_session, "never-ingested") == (
        "never-ingested"
    )
    assert await catalog_service.resolve_album_seokey(db_session, "") is None
    assert await catalog_service.resolve_album_seokey(db_session, "   ") is None


@pytest.mark.asyncio
async def test_songs_carry_the_album_id_the_client_navigates_with(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """End to end: a search result must expose an album_id that opens an album.

    Guards the join between the two halves -- the serializer emitting the field
    and the endpoint accepting it. Either one alone leaves the feature broken.
    """
    raw_track = {
        "seokey": "kuthanthram",
        "track_id": "kuthanthram",
        "title": "Kuthanthram",
        "artists": "Sushin Shyam",
        "artist_ids": "111",
        "album": "Manjummel Boys",
        "album_seokey": "manjummel-boys",
        "album_id": "8879668",
        "duration": "200",
        "language": "Malayalam",
        "genres": "Pop",
        "is_explicit": False,
        "images": {"urls": {}},
        "stream_urls": {"urls": {}},
    }

    async def search_songs(query, limit=10, *args, **kwargs):
        return [raw_track]

    async def get_album_info(seokeys, *args, **kwargs):
        assert seokeys == ["manjummel-boys"]
        return [GAANA_ALBUM_PAYLOAD]

    monkeypatch.setattr(catalog_service.gaana, "search_songs", search_songs)
    monkeypatch.setattr(catalog_service.gaana, "get_album_info", get_album_info)

    search = await client.get("/api/search?query=Kuthanthram&type=track", headers=auth_headers)
    assert search.status_code == 200, search.text
    song = search.json()["data"]["songs"][0]
    assert song["album_id"], "a song must carry the id its album can be opened by"

    album = await client.get(
        f"/api/catalog/albums/info?seokey={song['album_id']}", headers=auth_headers
    )
    assert album.status_code == 200, album.text
    assert album.json()["data"][0]["title"] == "Manjummel Boys"
