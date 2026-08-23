import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_theme.dart';
import '../../onboarding/application/onboarding_providers.dart';
import '../application/auth_providers.dart';

/// Resolves auth + onboarding state once at launch, then routes:
///   signed out            -> /login
///   signed in, onboarding incomplete -> /onboarding/languages
///   signed in, onboarding complete   -> /home
class SplashScreen extends ConsumerStatefulWidget {
  const SplashScreen({super.key});

  @override
  ConsumerState<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends ConsumerState<SplashScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _resolve());
  }

  Future<void> _resolve() async {
    try {
      final user = await ref.read(currentUserProvider.future);
      if (!mounted) return;
      if (user == null) {
        context.go('/login');
        return;
      }
      final status = await ref.read(onboardingStatusProvider.future);
      if (!mounted) return;
      if (status == null || !status.completed) {
        context.go('/onboarding/languages');
      } else {
        context.go('/home');
      }
    } catch (_) {
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
