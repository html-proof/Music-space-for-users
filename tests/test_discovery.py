import pytest
from unittest.mock import AsyncMock
import re

from api.discovery.discovery import Discovery
from api.errors import Errors
from api import endpoints
from app import ARTIST_ID_PATTERN


class FakeDiscovery(Discovery):
    def __init__(self, response):
        self.api_endpoints = endpoints
        self.errors = Errors()
        self._safe_request = AsyncMock(return_value=response)
        self.format_discovered_album = AsyncMock(
            return_value={"seokey": "album", "title": "Album"}
        )
        self.format_json_songs = AsyncMock(
            return_value={"seokey": "track", "stream_urls": {"urls": {"medium_quality": "url"}}}
        )


@pytest.mark.asyncio
async def test_similar_albums_are_formatted_with_required_headers():
    discovery = FakeDiscovery({"entities": [{"seokey": "album"}]})

    result = await discovery.get_similar_albums("123", 10)

    assert result == [{"seokey": "album", "title": "Album"}]
    call = discovery._safe_request.await_args
    assert call.args == ("GET", endpoints.similar_albums_url + "123")
    assert "User-Agent" in call.kwargs["headers"]


@pytest.mark.asyncio
async def test_artist_tracks_are_formatted_and_paginated_with_headers():
    discovery = FakeDiscovery({"tracks": [{"seokey": "track", "track_title": "Song"}], "total": 20})

    result = await discovery.get_artist_tracks("456", 5, 2)

    assert result["tracks"][0]["stream_urls"]["urls"]["medium_quality"] == "url"
    assert result["total"] == 20
    call = discovery._safe_request.await_args
    assert call.kwargs["params"] == {
        "sortBy": "popularity", "sortOrder": 0, "request_type": "web",
        "pkc": "true", "st": "hls", "song_type": "new", "limit": "5,5",
    }
    assert "Origin" in call.kwargs["headers"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("get_similar_albums", ("123", 10)),
    ("get_artist_tracks", ("456", 10, 1)),
])
async def test_empty_upstream_results_use_no_results_error(method, args):
    discovery = FakeDiscovery({})

    assert await getattr(discovery, method)(*args) == {
        "error": "Unable to find any results!"
    }


@pytest.mark.asyncio
async def test_upstream_error_is_returned_unchanged():
    discovery = FakeDiscovery({"error": "Unable to find any results!"})

@pytest.mark.parametrize("value", ["", "abc", "12-3"])
def test_id_validation_pattern_rejects_non_numeric_values(value):
    assert re.fullmatch(ARTIST_ID_PATTERN, value) is None
