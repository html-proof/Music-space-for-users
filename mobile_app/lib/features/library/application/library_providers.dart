import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/library_repository.dart';

final libraryRepositoryProvider = Provider<LibraryRepository>((ref) {
  return LibraryRepository(ref.watch(apiClientProvider));
});

final likedSongsProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(libraryRepositoryProvider).getLikedSongs();
});

final savedAlbumsProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(libraryRepositoryProvider).getSavedAlbums();
});

final followedArtistsProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(libraryRepositoryProvider).getFollowedArtists();
});
