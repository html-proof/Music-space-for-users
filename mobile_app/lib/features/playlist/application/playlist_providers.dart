import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/playlist_repository.dart';

final playlistRepositoryProvider = Provider<PlaylistRepository>((ref) {
  return PlaylistRepository(ref.watch(apiClientProvider));
});

final myPlaylistsProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(playlistRepositoryProvider).getMyPlaylists();
});

final playlistDetailProvider = FutureProvider.autoDispose.family((ref, String playlistId) {
  return ref.watch(playlistRepositoryProvider).getPlaylist(playlistId);
});
