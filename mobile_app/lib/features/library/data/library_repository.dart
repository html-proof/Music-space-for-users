import '../../../core/network/api_client.dart';
import '../../../shared/models/album.dart';
import '../../../shared/models/artist.dart';
import '../../../shared/models/song.dart';

/// Wraps /api/songs/{id}/like, /api/albums/{id}/save,
/// /api/artists/{id}/follow, and the /api/library/* collection endpoints
/// (app/api/library.py).
class LibraryRepository {
  LibraryRepository(this._api);

  final ApiClient _api;

  Future<bool> likeSong(String songId) async {
    final data = await _api.post('/api/songs/$songId/like') as Map<String, dynamic>;
    return data['is_liked'] == true;
  }

  Future<void> unlikeSong(String songId) => _api.delete('/api/songs/$songId/like');

  Future<List<Song>> getLikedSongs({int limit = 50, int offset = 0}) async {
    final data = await _api.get('/api/library/liked', query: {'limit': limit, 'offset': offset}) as List;
    return data.map((e) => Song.fromJson((e as Map).cast<String, dynamic>())).toList();
  }

  Future<void> saveAlbum(String albumId) => _api.post('/api/albums/$albumId/save');
  Future<void> unsaveAlbum(String albumId) => _api.delete('/api/albums/$albumId/save');

  Future<List<Album>> getSavedAlbums({int limit = 50, int offset = 0}) async {
    final data = await _api.get('/api/library/albums', query: {'limit': limit, 'offset': offset}) as List;
    return data.map((e) => Album.fromJson((e as Map).cast<String, dynamic>())).toList();
  }

  Future<void> followArtist(String artistId) => _api.post('/api/artists/$artistId/follow');
  Future<void> unfollowArtist(String artistId) => _api.delete('/api/artists/$artistId/follow');

  Future<List<Artist>> getFollowedArtists({int limit = 50, int offset = 0}) async {
    final data = await _api.get('/api/library/artists', query: {'limit': limit, 'offset': offset}) as List;
    return data.map((e) => Artist.fromJson((e as Map).cast<String, dynamic>())).toList();
  }
}
