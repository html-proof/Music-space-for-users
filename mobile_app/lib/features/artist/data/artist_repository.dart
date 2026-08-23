import '../../../core/network/api_client.dart';
import '../../../shared/models/artist.dart';
import '../../../shared/models/song.dart';

class ArtistDetails {
  final Artist artist;
  final List<Song> topTracks;

  const ArtistDetails({required this.artist, required this.topTracks});
}

/// Wraps GET /api/catalog/artists/info (raw Gaana passthrough, keyed by
/// seokey -- see api/artists/artists.py format_json_artists) and
/// GET /api/recommendations/similar-artist/{id}.
class ArtistRepository {
  ArtistRepository(this._api);

  final ApiClient _api;

  Future<ArtistDetails?> getDetails(String seokey) async {
    final data = await _api.get('/api/catalog/artists/info', query: {'seokey': seokey}) as List;
    if (data.isEmpty) return null;
    final raw = (data.first as Map).cast<String, dynamic>();
    final images = (raw['images'] as Map?)?['urls'] as Map? ?? const {};

    int parseCount(dynamic value) {
      if (value == null) return 0;
      return int.tryParse(value.toString()) ?? 0;
    }

    final artist = Artist(
      id: raw['artist_id']?.toString() ?? seokey,
      externalId: seokey,
      name: raw['name']?.toString() ?? '',
      seokey: seokey,
      imageUrl: (images['large_artwork'] ?? images['medium_artwork'])?.toString(),
      songCount: parseCount(raw['song_count']),
      albumCount: parseCount(raw['album_count']),
    );

    final topTracks = (raw['top_tracks'] as List? ?? const [])
        .whereType<Map>()
        .map((e) => Song.fromGaanaRaw(e.cast<String, dynamic>()))
        .toList();

    return ArtistDetails(artist: artist, topTracks: topTracks);
  }

  Future<List<Artist>> getSimilarArtists(String artistId, {int limit = 10}) async {
    final data = await _api.get('/api/recommendations/similar-artist/$artistId', query: {'limit': limit}) as List;
    return data.map((e) => Artist.fromJson((e as Map).cast<String, dynamic>())).toList();
  }
}
