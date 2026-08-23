import '../../../core/network/api_client.dart';
import '../../../shared/models/album.dart';
import '../../../shared/models/song.dart';

/// Wraps GET /api/catalog/albums/info (raw Gaana passthrough, keyed by
/// seokey -- see api/albums/albums.py format_json_albums).
class AlbumRepository {
  AlbumRepository(this._api);

  final ApiClient _api;

  Future<Album?> getDetails(String seokey) async {
    final data = await _api.get('/api/catalog/albums/info', query: {'seokey': seokey}) as List;
    if (data.isEmpty) return null;
    final raw = (data.first as Map).cast<String, dynamic>();
    final images = (raw['images'] as Map?)?['urls'] as Map? ?? const {};

    int parseCount(dynamic value) {
      if (value == null) return 0;
      return int.tryParse(value.toString()) ?? 0;
    }

    final tracks = (raw['tracks'] as List? ?? const [])
        .whereType<Map>()
        .map((e) => Song.fromGaanaRaw(e.cast<String, dynamic>()))
        .toList();

    return Album(
      id: raw['album_id']?.toString() ?? seokey,
      externalId: seokey,
      title: raw['title']?.toString() ?? '',
      seokey: seokey,
      artistName: raw['artists']?.toString() ?? '',
      coverUrl: (images['large_artwork'] ?? images['medium_artwork'])?.toString(),
      releaseDate: raw['release_date']?.toString(),
      language: raw['language']?.toString(),
      trackCount: parseCount(raw['track_count']),
      tracks: tracks,
    );
  }
}
