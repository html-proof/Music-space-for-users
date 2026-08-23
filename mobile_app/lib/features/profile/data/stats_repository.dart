import '../../../core/network/api_client.dart';

/// Wraps GET /api/me/top/artists (app/api/stats.py). Just the top-artists
/// slice for the profile tab's "your top artists" rail.
class StatsRepository {
  StatsRepository(this._api);

  final ApiClient _api;

  Future<List<Map<String, dynamic>>> topArtists({int limit = 10}) async {
    final data = await _api.get('/api/me/top/artists', query: {'limit': limit}) as Map<String, dynamic>;
    return (data['items'] as List? ?? const []).cast<Map<String, dynamic>>();
  }
}
