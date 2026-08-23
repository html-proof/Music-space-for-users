import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../../auth/application/auth_providers.dart';
import '../data/onboarding_repository.dart';

final onboardingRepositoryProvider = Provider<OnboardingRepository>((ref) {
  return OnboardingRepository(ref.watch(apiClientProvider));
});

/// Re-fetches whenever the signed-in user changes, and is invalidated
/// manually by the onboarding screens after each step so the splash/router
/// redirect logic sees fresh state immediately.
final onboardingStatusProvider = FutureProvider<OnboardingStatus?>((ref) async {
  final user = await ref.watch(currentUserProvider.future);
  if (user == null) return null;
  return ref.watch(onboardingRepositoryProvider).getStatus();
});
