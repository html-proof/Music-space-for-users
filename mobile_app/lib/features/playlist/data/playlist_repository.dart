import '../../../core/network/api_client.dart';
import '../../../shared/models/playlist.dart';

/// Wraps /api/playlists/* (app/api/playlists.py).
class PlaylistRepository {
  PlaylistRepository(this._api);

  final ApiClient _api;

  Future<List<Playlist>> getMyPlaylists({int limit = 50, int offset = 0}) async {
    final data = await _api.get('/api/playlists', query: {'limit': limit, 'offset': offset}) as List;
    return data.map((e) => Playlist.fromJson((e as Map).cast<String, dynamic>())).toList();
  }

  Future<Playlist> create({required String title, String? description, bool isPublic = false}) async {
    final data = await _api.post('/api/playlists', body: {
      'title': title,
      'description': description,
      'is_public': isPublic,
    });
    return Playlist.fromJson(data as Map<String, dynamic>);
  }

  Future<Playlist> getPlaylist(String playlistId) async {
    final data = await _api.get('/api/playlists/$playlistId');
    return Playlist.fromJson(data as Map<String, dynamic>);
  }

  Future<void> delete(String playlistId) => _api.delete('/api/playlists/$playlistId');

  Future<void> addSong(String playlistId, String songId) =>
      _api.post('/api/playlists/$playlistId/songs', body: {'song_id': songId});

  Future<void> removeSong(String playlistId, String songId) =>
      _api.delete('/api/playlists/$playlistId/songs/$songId');
}
