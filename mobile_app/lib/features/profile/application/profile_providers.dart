import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/stats_repository.dart';

final statsRepositoryProvider = Provider<StatsRepository>((ref) {
  return StatsRepository(ref.watch(apiClientProvider));
});

final topArtistsProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(statsRepositoryProvider).topArtists();
});
