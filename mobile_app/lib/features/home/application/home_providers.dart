import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/home_repository.dart';

final homeRepositoryProvider = Provider<HomeRepository>((ref) {
  return HomeRepository(ref.watch(apiClientProvider));
});

/// AsyncNotifier rather than a plain FutureProvider so pull-to-refresh can
/// force a genuine server-side recompute (`refresh: true`) and have the
/// result actually replace what's displayed -- `ref.invalidate` alone just
/// re-runs the same default (non-refresh) fetch, which the server can still
/// answer out of its own cache, making the "pull to refresh" gesture do
/// nothing visible.
class HomeFeedNotifier extends AutoDisposeAsyncNotifier<HomeFeed> {
  @override
  Future<HomeFeed> build() {
    return ref.watch(homeRepositoryProvider).getHomeFeed();
  }

  Future<void> refresh() async {
    state = const AsyncValue.loading();
    state = await AsyncValue.guard(
      () => ref.read(homeRepositoryProvider).getHomeFeed(refresh: true),
    );
  }
}

final homeFeedProvider = AsyncNotifierProvider.autoDispose<HomeFeedNotifier, HomeFeed>(
  HomeFeedNotifier.new,
);
