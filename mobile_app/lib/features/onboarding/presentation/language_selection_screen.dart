import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/config/app_config.dart';
import '../../../core/theme/app_theme.dart';
import '../application/onboarding_providers.dart';

class LanguageSelectionScreen extends ConsumerStatefulWidget {
  const LanguageSelectionScreen({super.key});

  @override
  ConsumerState<LanguageSelectionScreen> createState() => _LanguageSelectionScreenState();
}

class _LanguageSelectionScreenState extends ConsumerState<LanguageSelectionScreen> {
  final Set<String> _selected = {'English'};
  bool _saving = false;

  Future<void> _continue() async {
    if (_selected.isEmpty) return;
    setState(() => _saving = true);
    try {
      await ref.read(onboardingRepositoryProvider).setLanguages(_selected.toList());
      ref.invalidate(onboardingStatusProvider);
      if (!mounted) return;
      context.go('/onboarding/artists');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Choose your languages')),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              itemCount: AppConfig.selectableLanguages.length,
              itemBuilder: (context, index) {
                final lang = AppConfig.selectableLanguages[index];
                final checked = _selected.contains(lang);
                return CheckboxListTile(
                  value: checked,
                  activeColor: AppColors.accent,
                  title: Text(lang),
                  onChanged: (value) {
                    setState(() {
                      if (value == true) {
                        _selected.add(lang);
                      } else {
                        _selected.remove(lang);
                      }
                    });
                  },
                );
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _selected.isEmpty || _saving ? null : _continue,
                child: _saving
                    ? const SizedBox(
                        width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : const Text('Continue'),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
