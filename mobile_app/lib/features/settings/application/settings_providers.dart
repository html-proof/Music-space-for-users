import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/user_preferences_repository.dart';

final userPreferencesRepositoryProvider = Provider<UserPreferencesRepository>((ref) {
  return UserPreferencesRepository(ref.watch(apiClientProvider));
});

final userPreferencesProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(userPreferencesRepositoryProvider).get();
});
