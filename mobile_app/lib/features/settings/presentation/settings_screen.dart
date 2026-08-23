import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/config/app_config.dart';
import '../../../core/native/headset_safety_channel.dart';
import '../../../core/storage/local_database.dart';
import '../../../core/theme/app_theme.dart';
import '../../../core/theme/theme_mode_provider.dart';
import '../../auth/application/auth_providers.dart';
import '../application/settings_providers.dart';

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  String _qualityLabel(String value) => switch (value) {
        'low_quality' => 'Low',
        'medium_quality' => 'Normal',
        'high_quality' => 'High',
        'very_high_quality' => 'Very High',
        _ => value,
      };

  Future<void> _pickQuality(BuildContext context, WidgetRef ref, String current, void Function(String) onPicked) async {
    final picked = await showModalBottomSheet<String>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: AppConfig.audioQualities
              .map((q) => ListTile(
                    title: Text(_qualityLabel(q)),
                    trailing: q == current ? const Icon(Icons.check, color: AppColors.accent) : null,
                    onTap: () => Navigator.pop(context, q),
                  ))
              .toList(),
        ),
      ),
    );
    if (picked != null) onPicked(picked);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prefs = ref.watch(userPreferencesProvider);
    final themeMode = ref.watch(themeModeProvider);
    final repo = ref.read(userPreferencesRepositoryProvider);

    Future<void> patch(Map<String, dynamic> body) async {
      await repo.update(body);
      ref.invalidate(userPreferencesProvider);
    }

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: prefs.when(
        loading: () => const Center(child: CircularProgressIndicator(color: AppColors.accent)),
        error: (e, __) => Center(child: Text('$e')),
        data: (data) => ListView(
          children: [
            const _SectionTitle('Appearance'),
            RadioListTile<ThemeMode>(
              title: const Text('Light'),
              value: ThemeMode.light,
              groupValue: themeMode,
              onChanged: (v) => ref.read(themeModeProvider.notifier).setMode(v!),
            ),
            RadioListTile<ThemeMode>(
              title: const Text('Dark'),
              value: ThemeMode.dark,
              groupValue: themeMode,
              onChanged: (v) => ref.read(themeModeProvider.notifier).setMode(v!),
            ),
            RadioListTile<ThemeMode>(
              title: const Text('System'),
              value: ThemeMode.system,
              groupValue: themeMode,
              onChanged: (v) => ref.read(themeModeProvider.notifier).setMode(v!),
            ),
            const Divider(),
            const _SectionTitle('Audio'),
            ListTile(
              title: const Text('Streaming quality'),
              subtitle: Text(_qualityLabel(data.audioQuality)),
              onTap: () => _pickQuality(context, ref, data.audioQuality, (q) => patch({'audio_quality': q})),
            ),
            const Divider(),
            const _SectionTitle('Playback'),
            SwitchListTile(
              title: const Text('Autoplay'),
              subtitle: const Text('Keep playing similar songs when your music ends'),
              value: data.autoplay,
              onChanged: (v) => patch({'autoplay': v}),
            ),
            SwitchListTile(
              title: const Text('Explicit content'),
              subtitle: const Text('Allow explicit songs in recommendations and search'),
              value: data.explicitContent,
              onChanged: (v) => patch({'explicit_content': v}),
            ),
            SwitchListTile(
              title: const Text('Data saver'),
              subtitle: const Text('Reduce streaming quality on mobile data'),
              value: data.dataSaver,
              onChanged: (v) => patch({'data_saver': v}),
            ),
            ListTile(
              title: const Text('Crossfade'),
              subtitle: Text('${data.crossfade}s'),
              trailing: SizedBox(
                width: 160,
                child: Slider(
                  value: data.crossfade.toDouble(),
                  min: 0,
                  max: 12,
                  divisions: 12,
                  label: '${data.crossfade}s',
                  onChanged: (v) => patch({'crossfade': v.round()}),
                ),
              ),
            ),
            const Divider(),
            const _SectionTitle('Safety'),
            SwitchListTile(
              title: const Text('Headset safety reminder'),
              subtitle: const Text(
                'Notify me to take a break after 1 hour of continuous headset use',
              ),
              value: data.headsetSafetyReminder,
              onChanged: (v) async {
                await patch({'headset_safety_reminder': v});
                if (v) {
                  await HeadsetSafetyChannel.start();
                } else {
                  await HeadsetSafetyChannel.stop();
                }
              },
            ),
            const Divider(),
            const _SectionTitle('Storage'),
            const _StorageSection(),
            const Divider(),
            const _SectionTitle('Account'),
            ListTile(
              leading: const Icon(Icons.logout),
              title: const Text('Sign out'),
              onTap: () async {
                await ref.read(authRepositoryProvider).signOut();
                if (context.mounted) context.go('/login');
              },
            ),
            ListTile(
              leading: const Icon(Icons.delete_forever, color: AppColors.error),
              title: const Text('Delete account', style: TextStyle(color: AppColors.error)),
              onTap: () async {
                final confirmed = await showDialog<bool>(
                  context: context,
                  builder: (context) => AlertDialog(
                    title: const Text('Delete account?'),
                    content: const Text('This permanently deletes your account and all personal data.'),
                    actions: [
                      TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
                      TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Delete')),
                    ],
                  ),
                );
                if (confirmed == true) {
                  await ref.read(authRepositoryProvider).deleteAccount();
                  if (context.mounted) context.go('/login');
                }
              },
            ),
            const SizedBox(height: 32),
          ],
        ),
      ),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.title);
  final String title;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
      child: Text(title, style: const TextStyle(color: AppColors.accent, fontWeight: FontWeight.bold, fontSize: 13)),
    );
  }
}

class _StorageSection extends ConsumerStatefulWidget {
  const _StorageSection();

  @override
  ConsumerState<_StorageSection> createState() => _StorageSectionState();
}

class _StorageSectionState extends ConsumerState<_StorageSection> {
  int? _bytes;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final bytes = await LocalDatabase.instance.totalDownloadedBytes();
    if (mounted) setState(() => _bytes = bytes);
  }

  String _format(int bytes) {
    final mb = bytes / (1024 * 1024);
    if (mb >= 1024) return '${(mb / 1024).toStringAsFixed(2)} GB';
    return '${mb.toStringAsFixed(1)} MB';
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        ListTile(
          title: const Text('Downloaded songs'),
          subtitle: Text(_bytes == null ? '...' : _format(_bytes!)),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => context.push('/downloads'),
        ),
      ],
    );
  }
}
