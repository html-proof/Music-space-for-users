import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// Plain icon + label bottom nav -- the active tab is just accent-colored,
/// with no underline or pill indicator, matching the reference design's flat
/// nav treatment. Replaces Material's default BottomNavigationBar only to
/// keep sizing/spacing consistent with the rest of this custom theme.
class AppBottomNav extends StatelessWidget {
  const AppBottomNav({super.key, required this.currentIndex, required this.onTap});

  final int currentIndex;
  final ValueChanged<int> onTap;

  static const _items = [
    (icon: Icons.home_outlined, activeIcon: Icons.home, label: 'Home'),
    (icon: Icons.search, activeIcon: Icons.search, label: 'Search'),
    (icon: Icons.library_music_outlined, activeIcon: Icons.library_music, label: 'Library'),
    (icon: Icons.person_outline, activeIcon: Icons.person, label: 'Profile'),
  ];

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 58,
      child: Row(
        children: List.generate(_items.length, (index) {
          final item = _items[index];
          final active = index == currentIndex;
          final color = active ? AppColors.accent : AppColors.textSecondary;
          return Expanded(
            child: GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => onTap(index),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(active ? item.activeIcon : item.icon, color: color, size: 24),
                  const SizedBox(height: 4),
                  Text(
                    item.label,
                    style: TextStyle(
                      fontSize: 11,
                      color: color,
                      fontWeight: active ? FontWeight.w700 : FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ),
          );
        }),
      ),
    );
  }
}
