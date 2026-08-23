import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/network/api_exception.dart';
import '../../../core/theme/app_theme.dart';
import '../../onboarding/application/onboarding_providers.dart';
import '../application/auth_providers.dart';

/// Google Sign-In only -- there is deliberately no email/password form here.
/// The backend independently enforces this (rejects any Firebase token whose
/// sign_in_provider isn't google.com), so this screen simply never offers
/// another path in.
class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  bool _loading = false;
  String? _error;

  Future<void> _signIn() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      await ref.read(authRepositoryProvider).signInWithGoogle();
      // currentUserProvider re-syncs automatically off the Firebase auth
      // state change; wait for it so we know onboarding status before
      // routing. Bounded so a dead/slow backend can't leave the button
      // spinning forever -- the catch below surfaces it as a normal error.
      const timeout = Duration(seconds: 12);
      await ref.read(currentUserProvider.future).timeout(timeout);
      final status = await ref.read(onboardingStatusProvider.future).timeout(timeout);
      if (!mounted) return;
      if (status == null || !status.completed) {
        context.go('/onboarding/languages');
      } else {
        context.go('/home');
      }
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } catch (e) {
      // A bare "cancelled or failed" swallowed the real cause (most often a
      // PlatformException from google_sign_in -- e.g. ApiException: 10,
      // DEVELOPER_ERROR, which means the app's signing certificate's SHA-1
      // isn't registered on the Firebase Android app). Surface it so it's
      // actually diagnosable instead of guessing.
      debugPrint('Google sign-in failed: $e');
      setState(() => _error = 'Sign-in failed: $e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.graphic_eq, size: 72, color: AppColors.accent),
              const SizedBox(height: 20),
              const Text('GaanaPy', style: TextStyle(fontSize: 32, fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              const Text(
                'Your music, personalized.',
                style: TextStyle(color: AppColors.textSecondary),
              ),
              const SizedBox(height: 48),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed: _loading ? null : _signIn,
                  icon: _loading
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                        )
                      : const Icon(Icons.login),
                  label: const Text('Continue with Google'),
                ),
              ),
              if (_error != null) ...[
                const SizedBox(height: 16),
                Text(_error!, style: const TextStyle(color: AppColors.error), textAlign: TextAlign.center),
              ],
              const SizedBox(height: 32),
              const Text(
                'By continuing you agree to our Terms and Privacy Policy.',
                style: TextStyle(color: AppColors.textSecondary, fontSize: 12),
                textAlign: TextAlign.center,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
