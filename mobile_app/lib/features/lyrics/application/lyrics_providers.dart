import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/lyrics_repository.dart';

final lyricsRepositoryProvider = Provider<LyricsRepository>((ref) {
  return LyricsRepository(ref.watch(apiClientProvider));
});

final lyricsProvider = FutureProvider.autoDispose.family((ref, String songId) {
  return ref.watch(lyricsRepositoryProvider).getLyrics(songId);
});
