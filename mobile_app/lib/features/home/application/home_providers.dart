import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/home_repository.dart';

final homeRepositoryProvider = Provider<HomeRepository>((ref) {
  return HomeRepository(ref.watch(apiClientProvider));
});

final homeFeedProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(homeRepositoryProvider).getHomeFeed();
});
