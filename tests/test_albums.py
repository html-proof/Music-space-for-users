import pytest
import base64
from unittest.mock import AsyncMock
from Crypto.Cipher import AES
from api.functions import Functions
from api.errors import Errors
from api.albums.albums import Albums
from api.songs.songs import Songs
from api import endpoints


class FakeFormatter(Albums):
    def __init__(self):
        self.functions = AsyncMock(spec=Functions)
        self.errors = AsyncMock(spec=Errors)


def encrypted_link(plaintext: str) -> str:
    key = b'gy1t#b@jl(b$wtme'
    iv = b'0123456789abcdef'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return "1" + iv.decode() + base64.b64encode(cipher.encrypt(plaintext.encode())).decode()


class AlbumTracksFormatter(Albums, Songs):
    def __init__(self, responses):
        self.functions = Functions()
        self.errors = AsyncMock(spec=Errors)
        self.api_endpoints = endpoints
        self._safe_request = AsyncMock(side_effect=responses)


@pytest.mark.asyncio
async def test_format_json_albums():
    formatter = FakeFormatter()

    formatter.functions.findArtistNames.return_value = "Artist A, Artist B"
    formatter.functions.findArtistSeoKeys.return_value = "artist-a, artist-b"
    formatter.functions.findArtistIds.return_value = "id1, id2"
    formatter.functions.isExplicit.return_value = True

    gaana_input = {
        "album": {
            "seokey": "test-album-seokey",
            "album_id": "album-123",
            "title": "Greatest Hits",
            "artist": [{}],
            "duration": "3600",
            "parental_warning": 1,
            "language": "English",
            "recordlevel": "Test Label",
            "trackcount": 10,
            "release_date": "2020-01-01",
            "al_play_ct": 123456,
            "favorite_count": 7890,
            "artwork": "https://cdn.gaana.com/images/test/size_s.jpg"
        },
    }

    result = await formatter.format_json_albums(gaana_input)

    assert result["seokey"] == "test-album-seokey"
    assert result["images"]["urls"]["large_artwork"] == "https://cdn.gaana.com/images/test/size_l.jpg"
    assert "album_id" in result
    assert "album_url" in result
    assert "tracks" not in result


@pytest.mark.asyncio
async def test_format_json_albums_missing_keys():
    formatter = FakeFormatter()
    formatter.functions.findArtistNames.return_value = ""
    formatter.functions.findArtistSeoKeys.return_value = ""
    formatter.functions.findArtistIds.return_value = ""
    formatter.functions.isExplicit.return_value = False

    result = await formatter.format_json_albums({"album": {"seokey": "minimal"}})

    assert result["seokey"] == "minimal"
    assert result["album_id"] == ""
    assert result["title"] == ""
    assert result["artists"] == ""
    assert result["artist_seokeys"] == ""
    assert result["artist_ids"] == ""
    assert result["duration"] == ""
    assert result["is_explicit"] == False
    assert result["language"] == ""
    assert result["label"] == ""
    assert result["track_count"] == ""
    assert result["release_date"] == ""
    assert result["play_count"] == ""
    assert result["favorite_count"] == ""
    assert result["images"]["urls"]["large_artwork"] == ""
    assert result["images"]["urls"]["medium_artwork"] == ""
    assert result["images"]["urls"]["small_artwork"] == ""
    assert "tracks" not in result


@pytest.mark.asyncio
async def test_format_json_albums_no_album():
    formatter = FakeFormatter()
    formatter.errors.no_results.return_value = {"error": "Unable to find any results!"}

    result = await formatter.format_json_albums({})

    assert result == {"error": "Unable to find any results!"}


@pytest.mark.asyncio
async def test_format_json_albums_album_no_seokey():
    formatter = FakeFormatter()
    formatter.errors.no_results.return_value = {"error": "Unable to find any results!"}

    result = await formatter.format_json_albums({"album": {"title": "No Seokey"}})

    assert result == {"error": "Unable to find any results!"}


@pytest.mark.asyncio
async def test_format_json_albums_missing_artist_info():
    formatter = FakeFormatter()
    formatter.functions.isExplicit.return_value = False
    formatter.functions.findArtistNames.return_value = ""
    formatter.functions.findArtistSeoKeys.return_value = ""
    formatter.functions.findArtistIds.return_value = ""

    result = await formatter.format_json_albums({
        "album": {
            "seokey": "test",
            "album_id": "123",
            "title": "Test",
            "duration": "100",
            "parental_warning": 0,
            "language": "",
            "recordlevel": "",
            "trackcount": "",
            "al_play_ct": "",
            "favorite_count": "",
            "artwork": ""
        }
    })

    assert result["seokey"] == "test"
    assert result["artists"] == ""


@pytest.mark.asyncio
async def test_format_json_albums_info_includes_unpadded_track_stream_url():
    stream_url = "https://cdn.gaana.com/hls/64.mp4.master.m3u8?hdnts=deadbeef12345"
    assert len(stream_url.encode()) % 16 == 0
    track = {
        "seokey": "test-track",
        "urls": {"medium": {"message": encrypted_link(stream_url)}}
    }
    formatter = AlbumTracksFormatter([
        {"tracks": [{"seokey": "test-track"}]},
        {"tracks": [track]},
    ])

    result = await formatter.format_json_albums(
        {"album": {"seokey": "test-album"}}, info=True
    )

    assert result["tracks"][0]["stream_urls"]["urls"]["medium_quality"] == stream_url
