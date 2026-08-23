import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/playback_repository.dart';
import 'player_controller.dart';

final playbackRepositoryProvider = Provider<PlaybackRepository>((ref) {
  return PlaybackRepository(ref.watch(apiClientProvider));
});

/// One player instance for the app's lifetime -- created lazily on first
/// use and kept alive so playback survives navigation between screens.
final playerControllerProvider = ChangeNotifierProvider<PlayerController>((ref) {
  return PlayerController(ref.watch(playbackRepositoryProvider), ref.watch(localDatabaseProvider));
});
