import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/storage/local_preferences_cache.dart';
import '../../../core/theme/app_theme.dart';
import '../../onboarding/application/onboarding_providers.dart';
import '../application/auth_providers.dart';

/// Resolves auth + onboarding state at launch, then routes:
///   signed out                       -> /login
///   signed in, onboarding incomplete -> /onboarding/languages
///   signed in, onboarding complete   -> /home
///
/// A returning user who was already fully onboarded skips the network round
/// trip entirely: once Firebase's own (local-only) session restore confirms
/// who is signed in, LocalPreferencesCache already knows onboarding finished
/// from the last successful fetch -- so there is nothing left to *decide*,
/// only to confirm in the background once home screen providers load. Every
/// other case (first launch, signed out, an interrupted onboarding, or the
/// cache genuinely not knowing yet) goes through the full check below, which
/// is itself bounded so a slow or dead network can never leave this screen
/// spinning forever.
class SplashScreen extends ConsumerStatefulWidget {
  const SplashScreen({super.key});

  @override
  ConsumerState<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends ConsumerState<SplashScreen> {
  // Matches ApiClient's connectTimeout: generous enough to cover a Render
  // free-tier cold start (the backend spins down on inactivity), not just a
  // warm response.
  static const _resolveTimeout = Duration(seconds: 45);
  // Firebase restoring a previously-signed-in session from disk is local --
  // no network involved -- so this only needs to cover that, not a cold
  // backend. Bounded so a wedged native SDK call still can't hang forever.
  static const _sessionRestoreTimeout = Duration(seconds: 5);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _resolve());
  }

  Future<void> _resolve() async {
    User? firebaseUser;
    try {
      // Awaits Firebase's own restore of a persisted session rather than
      // reading the synchronous `.currentUser` getter directly -- that
      // getter races the native SDK's restore on cold start and is often
      // still null for a genuinely signed-in user at this exact moment,
      // which silently skipped the fast path below and forced a full
      // network round trip (auth + onboarding status) on nearly every
      // launch. This is a local disk read, never network, hence the short
      // timeout.
      firebaseUser = await ref.read(firebaseUserProvider.future).timeout(_sessionRestoreTimeout);
    } catch (_) {
      firebaseUser = ref.read(authRepositoryProvider).firebaseUser;
    }

    if (firebaseUser == null) {
      if (!mounted) return;
      context.go('/login');
      return;
    }

    final cachedCompleted = await LocalPreferencesCache.getOnboardingCompleted();
    if (cachedCompleted == true) {
      if (!mounted) return;
      context.go('/home');
      return;
    }

    try {
      final user = await ref.read(currentUserProvider.future).timeout(_resolveTimeout);
      if (!mounted) return;
      if (user == null) {
        context.go('/login');
        return;
      }
      final status = await ref.read(onboardingStatusProvider.future).timeout(_resolveTimeout);
      if (!mounted) return;
      if (status == null || !status.completed) {
        context.go('/onboarding/languages');
      } else {
        context.go('/home');
      }
    } catch (_) {
      // Covers both a real failure and a timeout (an unreachable backend
      // must never leave the user stuck on a spinner) -- fail safe to the
      // login screen, which is where an unauthenticated or unconfirmed
      // session belongs.
      if (mounted) context.go('/login');
    }
  }

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      backgroundColor: AppColors.background,
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.graphic_eq, size: 64, color: AppColors.accent),
            SizedBox(height: 16),
            Text('GaanaPy', style: TextStyle(fontSize: 28, fontWeight: FontWeight.bold, color: AppColors.textPrimary)),
            SizedBox(height: 24),
            CircularProgressIndicator(color: AppColors.accent),
          ],
        ),
      ),
    );
  }
}
