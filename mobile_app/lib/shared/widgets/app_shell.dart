import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/native/headset_safety_channel.dart';
import '../../features/player/presentation/mini_player.dart';
import '../../features/settings/application/settings_providers.dart';

/// Bottom-nav shell (Home / Search / Library / Profile) with the persistent
/// mini-player docked above the nav bar, matching the "mini player above
/// bottom nav" layout from the spec.
class AppShell extends ConsumerStatefulWidget {
  const AppShell({super.key, required this.navigationShell});

  final StatefulNavigationShell navigationShell;

  @override
  ConsumerState<AppShell> createState() => _AppShellState();
}

class _AppShellState extends ConsumerState<AppShell> {
  @override
  void initState() {
    super.initState();
    // Sync the headset safety foreground service to the user's saved
    // preference once, on first reaching the authenticated app shell --
    // not tied to the Settings screen ever being opened.
    WidgetsBinding.instance.addPostFrameCallback((_) => _syncHeadsetSafety());
  }

  Future<void> _syncHeadsetSafety() async {
    try {
      final prefs = await ref.read(userPreferencesProvider.future);
      if (prefs.headsetSafetyReminder) {
        await HeadsetSafetyChannel.start();
      } else {
        await HeadsetSafetyChannel.stop();
      }
    } catch (_) {
      // Best-effort: a failed preferences fetch here must not block the app
      // from rendering the home shell.
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: widget.navigationShell,
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.only(bottom: 4),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Padding(
                padding: EdgeInsets.only(bottom: 6),
                child: MiniPlayer(),
              ),
              BottomNavigationBar(
                currentIndex: widget.navigationShell.currentIndex,
                onTap: (index) => widget.navigationShell.goBranch(
                  index,
                  initialLocation: index == widget.navigationShell.currentIndex,
                ),
                items: const [
                  BottomNavigationBarItem(icon: Icon(Icons.home_outlined), activeIcon: Icon(Icons.home), label: 'Home'),
                  BottomNavigationBarItem(icon: Icon(Icons.search), label: 'Search'),
                  BottomNavigationBarItem(icon: Icon(Icons.library_music_outlined), activeIcon: Icon(Icons.library_music), label: 'Library'),
                  BottomNavigationBarItem(icon: Icon(Icons.person_outline), activeIcon: Icon(Icons.person), label: 'Profile'),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
