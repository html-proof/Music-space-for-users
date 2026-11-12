import asyncio

# Gaana sits behind Akamai, which answers a burst of parallel requests with
# 403s. An album's tracks used to be re-fetched one songDetail request each,
# all at once, so a 20-track album fired 20+ requests in the same instant: some
# came back denied and those tracks were dropped without a trace, and once the
# limiter had tripped the album came back with no tracks at all.
#
# albumDetail already returns every track in full, stream URLs included, so
# nothing normally needs fetching. This bounds the fallback for the entries
# that do arrive incomplete.
TRACK_DETAIL_CONCURRENCY = 4


def _has_stream(track: dict) -> bool:
    urls = track.get('urls')
    medium = urls.get('medium') if isinstance(urls, dict) else None
    return isinstance(medium, dict) and bool(medium.get('message'))


class Albums:
    async def search_albums(self, search_query: str, limit: int) -> list:
        endpoints = self.api_endpoints
        errors = self.errors
        result = await self._safe_request("POST", endpoints.search_albums_url + search_query)
        if isinstance(result, dict) and "error" in result:
            return result
        album_ids = []
        gd = (result.get('gr') or [{}])[0].get('gd', [])
        for i in range(min(limit, len(gd))):
            seo = gd[i].get('seo') if isinstance(gd[i], dict) else None
            if seo:
                album_ids.append(seo)
        if len(album_ids) == 0:
            return await errors.no_results()
        album_info = await self.get_album_info(album_ids, False)
        return album_info

    async def get_album_info(self, album_id: list, info: bool) -> list:
        endpoints = self.api_endpoints
        errors = self.errors
        results = await asyncio.gather(*[
            self._safe_request("POST", endpoints.album_details_url + i)
            for i in album_id
        ])
        album_info = []
        for result in results:
            if isinstance(result, dict) and "error" in result:
                continue
            album_info.append(await self.format_json_albums(result, info=info))
        if len(album_info) == 0:
            return await errors.no_results()
        return album_info

    async def get_album_tracks(self, album_id: str) -> list:
        endpoints = self.api_endpoints
        result = await self._safe_request("POST", endpoints.album_details_url + album_id)
        if isinstance(result, dict) and "error" in result:
            return result
        tracks = await self.format_album_tracks(result)
        if len(tracks) == 0:
            return await self.errors.no_results()
        return tracks

    async def format_album_tracks(self, result: dict) -> list:
        """The tracks of one albumDetail response, in album order.

        Formatted straight from the response wherever it describes the track in
        full. Only an entry with no stream payload costs a songDetail request,
        and a failed one costs that track its stream URL rather than its place
        in the list. Always a list, empty when the album names no tracks.
        """
        endpoints = self.api_endpoints
        raw_tracks = [
            track for track in (result.get('tracks') or [])
            if isinstance(track, dict) and track.get('seokey')
        ]

        incomplete = list(dict.fromkeys(
            track['seokey'] for track in raw_tracks if not _has_stream(track)
        ))
        detailed = {}
        if incomplete:
            limiter = asyncio.Semaphore(TRACK_DETAIL_CONCURRENCY)

            async def fetch(seokey: str) -> dict:
                async with limiter:
                    return await self._safe_request("POST", endpoints.song_details_url + seokey)

            responses = await asyncio.gather(*[fetch(seokey) for seokey in incomplete])
            for seokey, response in zip(incomplete, responses):
                found = response.get('tracks') if isinstance(response, dict) else None
                if found and isinstance(found[0], dict) and found[0].get('seokey'):
                    detailed[seokey] = found[0]

        album = result.get('album')
        album_label = album.get('recordlevel', '') if isinstance(album, dict) else ''

        tracks = []
        for raw in raw_tracks:
            source = detailed.get(raw['seokey'])
            if source is None:
                if not raw.get('track_title'):
                    # A bare reference whose detail lookup failed: nothing to show.
                    continue
                source = raw
            track = await self.format_json_songs(source)
            if "error" in track:
                continue
            # songDetail carries these two per track; albumDetail carries the
            # label on the album and the artist image on the credit instead.
            if not track.get('label'):
                track['label'] = album_label or ''
            if not track.get('artist_image'):
                credits = source.get('artist')
                first = credits[0] if isinstance(credits, list) and credits else None
                track['artist_image'] = first.get('atw', '') if isinstance(first, dict) else ''
            tracks.append(track)
        return tracks

    async def format_json_albums(self, results: dict, info: bool = False) -> dict:
        functions = self.functions
        errors = self.errors
        data = {}

        album = results.get('album')
        if not album or not album.get('seokey'):
            return await errors.no_results()

        data['seokey'] = album['seokey']
        data['album_id'] = album.get('album_id', '')
        data['title'] = album.get('title', '')
        try:
            data['artists'] = await functions.findArtistNames(
                album.get('artist') or []
            )
            tracks = results.get('tracks')
            first_track_artist = (
                tracks[0].get('artist') or []
                if tracks and isinstance(tracks, list) and len(tracks) > 0
                else []
            )
            data['artist_seokeys'] = await functions.findArtistSeoKeys(first_track_artist)
            data['artist_ids'] = await functions.findArtistIds(first_track_artist)
        except (KeyError, IndexError):
            data['artists'] = ""
            data['artist_seokeys'] = ""
            data['artist_ids'] = ""
        data['duration'] = album.get('duration', '')
        data['is_explicit'] = await functions.isExplicit(
            album.get('parental_warning', 0)
        )
        data['language'] = album.get('language', '')
        data['label'] = album.get('recordlevel', '')
        data['track_count'] = album.get('trackcount', '')
        data['release_date'] = album.get('release_date', '')
        data['play_count'] = album.get('al_play_ct', '')
        data['favorite_count'] = album.get('favorite_count', '')
        data['album_url'] = f"https://gaana.com/album/{data['seokey']}"
        artwork = album.get('artwork', '')
        data['images'] = {'urls': {}}
        data['images']['urls']['large_artwork'] = artwork.replace("size_s.jpg", "size_l.jpg") if artwork else ''
        data['images']['urls']['medium_artwork'] = artwork.replace("size_s.jpg", "size_m.jpg") if artwork else ''
        data['images']['urls']['small_artwork'] = artwork

        if info:
            if isinstance(results.get('tracks'), list):
                # The response in hand already lists the tracks; fetching the
                # album a second time only to read them back doubled the load.
                data['tracks'] = await self.format_album_tracks(results)
            else:
                tracks = await self.get_album_tracks(data['seokey'])
                # Never an error dict: clients iterate this.
                data['tracks'] = tracks if isinstance(tracks, list) else []
        return data
