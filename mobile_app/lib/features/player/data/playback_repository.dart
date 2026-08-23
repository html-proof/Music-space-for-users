import '../../../core/network/api_client.dart';

/// Wraps /api/player/* (app/api/player.py). This is cross-device playback
/// *state sync*, not the audio path itself -- the actual bytes are streamed
/// locally by just_audio directly from the song's stream URL (or a
/// downloaded file). Calls here are fire-and-forget from the player
/// controller so a second signed-in device can see "what's playing now" via
/// GET /api/player/current.
class PlaybackRepository {
  PlaybackRepository(this._api);

  final ApiClient _api;

  Future<void> reportEvent({
    required String deviceId,
    required String songId,
    required String event,
    double position = 0,
    double duration = 0,
  }) async {
    await _api.post('/api/player/events', body: {
      'device_id': deviceId,
      'song_id': songId,
      'event': event,
      'position': position,
      'duration': duration,
    });
  }

  Future<void> sync({
    required String deviceId,
    String? songId,
    String? playlistId,
    double positionSeconds = 0,
    double durationSeconds = 0,
    String state = 'playing',
    int volume = 100,
    bool shuffle = false,
    String repeatMode = 'off',
    List<String>? queue,
  }) async {
    await _api.post('/api/player/sync', body: {
      'device_id': deviceId,
      if (songId != null) 'song_id': songId,
      if (playlistId != null) 'playlist_id': playlistId,
      'position_seconds': positionSeconds,
      'duration_seconds': durationSeconds,
      'state': state,
      'volume': volume,
      'shuffle': shuffle,
      'repeat_mode': repeatMode,
      if (queue != null) 'queue': queue,
    });
  }
}
