import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// Underline-indicator bottom nav (icon + label, active tab gets an accent
/// underline bar beneath it) -- replaces Material's default
/// BottomNavigationBar, whose active state is a colored icon/label with no
/// separate indicator, to match the reference design's nav treatment.
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
                  const SizedBox(height: 4),
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 180),
                    height: 3,
                    width: active ? 18 : 0,
                    decoration: BoxDecoration(
                      color: AppColors.accent,
                      borderRadius: BorderRadius.circular(2),
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
