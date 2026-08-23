import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_theme.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../application/onboarding_providers.dart';

class LanguageSelectionScreen extends ConsumerStatefulWidget {
  const LanguageSelectionScreen({super.key});

  @override
  ConsumerState<LanguageSelectionScreen> createState() => _LanguageSelectionScreenState();
}

class _LanguageSelectionScreenState extends ConsumerState<LanguageSelectionScreen> {
  final Set<String> _selected = {};
  bool _saving = false;

  Future<void> _continue() async {
    setState(() => _saving = true);
    try {
      // Nothing selected -- either the catalog has no languages yet (a
      // freshly deployed/thin backend) or the user chose not to pick one.
      // Either way there is nothing valid to save, so this just moves on
      // rather than blocking onboarding on data the backend doesn't have.
      if (_selected.isNotEmpty) {
        await ref.read(onboardingRepositoryProvider).setLanguages(_selected.toList());
        ref.invalidate(onboardingStatusProvider);
      }
      if (!mounted) return;
      context.go('/onboarding/artists');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final languages = ref.watch(onboardingLanguagesProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Choose your languages')),
      body: Column(
        children: [
          Expanded(
            child: AsyncValueView(
              value: languages,
              onRetry: () => ref.invalidate(onboardingLanguagesProvider),
              data: (options) {
                // A genuinely empty catalog is expected on a fresh/thin
                // backend -- languages are derived from songs actually
                // ingested, not a maintained list, so there is nothing fake
                // to show here instead.
                if (options.isEmpty) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(32),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Icon(Icons.language, color: AppColors.textSecondary, size: 40),
                          const SizedBox(height: 12),
                          const Text(
                            'No languages available yet -- the music catalog is still filling up. You can continue and pick one later.',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: AppColors.textSecondary),
                          ),
                        ],
                      ),
                    ),
                  );
                }
                // No client-side default selection -- the backend's language
                // list carries no notion of a "preferred" entry, so picking
                // one for the user (even just to pre-check a box) would be
                // exactly the kind of preset content this screen exists to
                // avoid. The user chooses.
                return ListView.builder(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  itemCount: options.length,
                  itemBuilder: (context, index) {
                    final lang = options[index];
                    final checked = _selected.contains(lang.name);
                    return CheckboxListTile(
                      value: checked,
                      activeColor: AppColors.accent,
                      title: Text(lang.name),
                      onChanged: (value) {
                        setState(() {
                          if (value == true) {
                            _selected.add(lang.name);
                          } else {
                            _selected.remove(lang.name);
                          }
                        });
                      },
                    );
                  },
                );
              },
            ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _saving ? null : _continue,
                  child: _saving
                      ? const SizedBox(
                          width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : Text(_selected.isEmpty ? 'Skip for now' : 'Continue'),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
