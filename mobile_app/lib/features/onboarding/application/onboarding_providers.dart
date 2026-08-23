import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../../../core/storage/local_preferences_cache.dart';
import '../../../shared/models/artist.dart';
import '../../../shared/models/language.dart';
import '../../auth/application/auth_providers.dart';
import '../data/onboarding_repository.dart';

final onboardingRepositoryProvider = Provider<OnboardingRepository>((ref) {
  return OnboardingRepository(ref.watch(apiClientProvider));
});

final onboardingLanguagesProvider = FutureProvider.autoDispose<List<LanguageOption>>((ref) {
  return ref.watch(onboardingRepositoryProvider).getLanguages();
});

final suggestedArtistsProvider = FutureProvider.autoDispose<List<Artist>>((ref) {
  return ref.watch(onboardingRepositoryProvider).getSuggestedArtists();
});

/// Re-fetches whenever the signed-in user changes, and is invalidated
/// manually by the onboarding screens after each step so the splash/router
/// redirect logic sees fresh state immediately.
///
/// The backend is always the source of truth: on success this writes
/// through to LocalPreferencesCache, and only falls back to that cache if
/// the backend call itself fails (offline cold start) -- it never serves
/// the cache preferentially over a live answer.
final onboardingStatusProvider = FutureProvider<OnboardingStatus?>((ref) async {
  final user = await ref.watch(currentUserProvider.future);
  if (user == null) return null;

  try {
    final status = await ref.watch(onboardingRepositoryProvider).getStatus();
    await LocalPreferencesCache.saveLanguages(status.preferredLanguages);
    await LocalPreferencesCache.saveArtists(status.favoriteArtists);
    await LocalPreferencesCache.saveOnboardingCompleted(status.completed);
    return status;
  } catch (e) {
    final cachedCompleted = await LocalPreferencesCache.getOnboardingCompleted();
    if (cachedCompleted == null) rethrow;
    return OnboardingStatus(
      completed: cachedCompleted,
      preferredLanguages: await LocalPreferencesCache.getLanguages() ?? const [],
      favoriteArtists: await LocalPreferencesCache.getArtists() ?? const [],
    );
  }
});
