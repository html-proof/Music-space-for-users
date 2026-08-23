import '../../../core/network/api_client.dart';
import '../../../shared/models/lyrics.dart';

/// Wraps GET /api/songs/{id}/lyrics (app/api/lyrics.py). Only reads --
/// writing lyrics is an admin-token-gated backend operation, not something
/// the client does.
class LyricsRepository {
  LyricsRepository(this._api);

  final ApiClient _api;

  Future<LyricsData> getLyrics(String songId) async {
    final data = await _api.get('/api/songs/$songId/lyrics');
    return LyricsData.fromJson(data as Map<String, dynamic>);
  }
}
