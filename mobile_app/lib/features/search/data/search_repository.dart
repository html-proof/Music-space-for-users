import '../../../core/network/api_client.dart';
import '../../../shared/models/album.dart';
import '../../../shared/models/artist.dart';
import '../../../shared/models/song.dart';

class SearchResults {
  final List<Song> songs;
  final List<Album> albums;
  final List<Artist> artists;

  const SearchResults({this.songs = const [], this.albums = const [], this.artists = const []});

  bool get isEmpty => songs.isEmpty && albums.isEmpty && artists.isEmpty;
}

/// Wraps /api/search (app/api/search.py).
class SearchRepository {
  SearchRepository(this._api);

  final ApiClient _api;

  Future<SearchResults> search(String query, {String type = 'all', int limit = 20}) async {
    final data = await _api.get('/api/search', query: {
      'query': query,
      'type': type,
      'limit': limit,
    }) as Map<String, dynamic>;

    return SearchResults(
      songs: (data['songs'] as List? ?? const [])
          .map((e) => Song.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
      albums: (data['albums'] as List? ?? const [])
          .map((e) => Album.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
      artists: (data['artists'] as List? ?? const [])
          .map((e) => Artist.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
    );
  }

  Future<List<String>> suggest(String query, {int limit = 10}) async {
    final data = await _api.get('/api/search/suggest', query: {
      'query': query,
      'limit': limit,
    }) as Map<String, dynamic>;
    return (data['suggestions'] as List? ?? const []).map((e) => e.toString()).toList();
  }

  Future<List<String>> getHistory({int limit = 20}) async {
    final data = await _api.get('/api/search/history', query: {'limit': limit}) as List;
    return data.map((e) => (e as Map)['query'].toString()).toList();
  }

  Future<void> clearHistory() => _api.delete('/api/search/history');
}
