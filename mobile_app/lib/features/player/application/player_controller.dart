import 'dart:async' show unawaited;

import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart';

import '../../../core/storage/device_id.dart';
import '../../../core/storage/local_database.dart';
import '../../../shared/models/song.dart';
import '../data/playback_repository.dart';

/// Owns the actual audio session for this device and mirrors it into a
/// small amount of observable state for the UI (mini player, now-playing
/// screen, lyrics auto-scroll).
///
/// just_audio's `ConcatenatingAudioSource` already implements queue/shuffle/
/// repeat, so this class is mostly translation: which local file or stream
/// URL backs each Song, and fire-and-forget telemetry to the backend so a
/// second device can see what's playing (see PlaybackRepository).
class PlayerController extends ChangeNotifier {
  PlayerController(this._playbackRepository, this._localDb) {
    _player.currentIndexStream.listen((_) => notifyListeners());
    _player.playerStateStream.listen((_) => notifyListeners());
    _player.positionStream.listen((_) => notifyListeners());
    _player.shuffleModeEnabledStream.listen((_) => notifyListeners());
    _player.loopModeStream.listen((_) => notifyListeners());
    _player.processingStateStream.listen((state) {
      if (state == ProcessingState.completed) {
        _reportEvent('COMPLETE');
      }
    });
  }

  final PlaybackRepository _playbackRepository;
  final LocalDatabase _localDb;
  final AudioPlayer _player = AudioPlayer();

  List<Song> _queue = [];
  String _audioQuality = 'high_quality';

  List<Song> get queue => _queue;
  Song? get currentSong {
    final index = _player.currentIndex;
    if (index == null || index < 0 || index >= _queue.length) return null;
    return _queue[index];
  }

  bool get isPlaying => _player.playing;
  Duration get position => _player.position;
  Duration? get duration => _player.duration;
  bool get shuffleEnabled => _player.shuffleModeEnabled;
  LoopMode get loopMode => _player.loopMode;
  Stream<Duration> get positionStream => _player.positionStream;

  void setAudioQuality(String quality) => _audioQuality = quality;

  Future<void> playQueue(List<Song> songs, {int startIndex = 0}) async {
    if (songs.isEmpty) return;
    _queue = songs;

    final sources = <AudioSource>[];
    for (final song in songs) {
      final local = await _localDb.getDownloadedFile(song.id);
      final uri = local != null ? Uri.file(local['local_path'] as String) : Uri.parse(_resolveUrl(song));
      sources.add(AudioSource.uri(uri, tag: song.id));
    }

    await _player.setAudioSource(
      ConcatenatingAudioSource(children: sources),
      initialIndex: startIndex,
    );
    await _player.play();
    unawaited(_reportEvent('PLAY'));
  }

  String _resolveUrl(Song song) {
    return song.streamUrlFor(_audioQuality) ??
        song.streamUrlFor('high_quality') ??
        song.audioUrl ??
        '';
  }

  Future<void> togglePlayPause() async {
    if (_player.playing) {
      await _player.pause();
      unawaited(_reportEvent('PAUSE'));
    } else {
      await _player.play();
      unawaited(_reportEvent('RESUME'));
    }
  }

  Future<void> seek(Duration position) async {
    await _player.seek(position);
    unawaited(_reportEvent('SEEK'));
  }

  Future<void> next() async {
    if (_player.hasNext) {
      await _player.seekToNext();
      await _player.play();
      unawaited(_reportEvent('NEXT'));
    }
  }

  Future<void> previous() async {
    // Restart the current track if we're more than 3s in, matching the
    // convention every streaming player uses for the "previous" control.
    if (_player.position > const Duration(seconds: 3)) {
      await _player.seek(Duration.zero);
      return;
    }
    if (_player.hasPrevious) {
      await _player.seekToPrevious();
      await _player.play();
      unawaited(_reportEvent('PREVIOUS'));
    } else {
      await _player.seek(Duration.zero);
    }
  }

  Future<void> toggleShuffle() => _player.setShuffleModeEnabled(!_player.shuffleModeEnabled);

  Future<void> cycleRepeatMode() {
    final next = switch (_player.loopMode) {
      LoopMode.off => LoopMode.all,
      LoopMode.all => LoopMode.one,
      LoopMode.one => LoopMode.off,
    };
    return _player.setLoopMode(next);
  }

  Future<void> stop() async {
    await _player.stop();
    unawaited(_reportEvent('STOP'));
  }

  Future<void> _reportEvent(String event) async {
    final song = currentSong;
    if (song == null) return;
    try {
      final deviceId = await DeviceIdProvider.get();
      await _playbackRepository.reportEvent(
        deviceId: deviceId,
        songId: song.id,
        event: event,
        position: position.inMilliseconds / 1000,
        duration: (duration ?? Duration.zero).inMilliseconds / 1000,
      );
    } catch (_) {
      // Telemetry only -- never let a failed report interrupt playback.
    }
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }
}
