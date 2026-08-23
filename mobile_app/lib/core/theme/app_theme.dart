import 'package:flutter/material.dart';

/// A warm, editorial palette -- vivid orange as the primary accent, with a
/// rotating set of color-blocked tile colors (teal/pink/navy/gold) for
/// artwork placeholders, on a near-black background. Not Spotify green, and
/// not a single flat accent either: closer to a bold color-blocked poster
/// than a typical music-app UI.
class AppColors {
  AppColors._();

  static const accent = Color(0xFFFF7A3D);
  static const accentMuted = Color(0xFFB8541F);
  static const background = Color(0xFF0B0B12);
  static const surface = Color(0xFF16161F);
  static const surfaceRaised = Color(0xFF1F1F2C);
  static const textPrimary = Color(0xFFF5F5FA);
  static const textSecondary = Color(0xFFA0A0B2);
  static const success = Color(0xFF3ECF8E);
  static const error = Color(0xFFEF5B5B);

  /// Rotating background colors for artwork tiles that have no real image
  /// yet (see MediaCard) -- deliberately vivid, matching the color-blocked
  /// playlist/artist tiles in the reference design rather than a flat gray box.
  static const tileColors = [
    Color(0xFFFF7A3D), // orange
    Color(0xFF1FA98C), // teal
    Color(0xFFFF6F91), // coral pink
    Color(0xFF2C2F6B), // navy
    Color(0xFFE8A33D), // gold
  ];

  /// A stable tile color for a given key (song/artist/album id or title),
  /// so the same item always gets the same color across rebuilds/screens
  /// rather than a random one on every frame.
  static Color tileColorFor(String key) => tileColors[key.hashCode.abs() % tileColors.length];

  // Light-theme counterparts.
  static const backgroundLight = Color(0xFFFAFAFC);
  static const surfaceLight = Color(0xFFFFFFFF);
  static const surfaceRaisedLight = Color(0xFFF0F0F5);
  static const textPrimaryLight = Color(0xFF16161F);
  static const textSecondaryLight = Color(0xFF62626E);
}

class AppTheme {
  AppTheme._();

  static ThemeData get dark {
    final base = ThemeData.dark(useMaterial3: true);
    return base.copyWith(
      scaffoldBackgroundColor: AppColors.background,
      colorScheme: base.colorScheme.copyWith(
        primary: AppColors.accent,
        secondary: AppColors.accentMuted,
        surface: AppColors.surface,
        error: AppColors.error,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.background,
        elevation: 0,
        foregroundColor: AppColors.textPrimary,
      ),
      cardTheme: const CardThemeData(
        color: AppColors.surfaceRaised,
        elevation: 0,
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: AppColors.surface,
        selectedItemColor: AppColors.accent,
        unselectedItemColor: AppColors.textSecondary,
        type: BottomNavigationBarType.fixed,
      ),
      textTheme: base.textTheme.apply(
        bodyColor: AppColors.textPrimary,
        displayColor: AppColors.textPrimary,
      ),
      sliderTheme: base.sliderTheme.copyWith(
        activeTrackColor: AppColors.accent,
        thumbColor: AppColors.accent,
        inactiveTrackColor: AppColors.surfaceRaised,
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: AppColors.accent,
          foregroundColor: Colors.white,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
        ),
      ),
      chipTheme: base.chipTheme.copyWith(
        backgroundColor: AppColors.surfaceRaised,
        selectedColor: AppColors.accent,
        labelStyle: const TextStyle(color: AppColors.textPrimary),
      ),
    );
  }

  static ThemeData get light {
    final base = ThemeData.light(useMaterial3: true);
    return base.copyWith(
      scaffoldBackgroundColor: AppColors.backgroundLight,
      colorScheme: base.colorScheme.copyWith(
        primary: AppColors.accent,
        secondary: AppColors.accentMuted,
        surface: AppColors.surfaceLight,
        error: AppColors.error,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: AppColors.backgroundLight,
        elevation: 0,
        foregroundColor: AppColors.textPrimaryLight,
      ),
      cardTheme: const CardThemeData(
        color: AppColors.surfaceRaisedLight,
        elevation: 0,
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: AppColors.surfaceLight,
        selectedItemColor: AppColors.accent,
        unselectedItemColor: AppColors.textSecondaryLight,
        type: BottomNavigationBarType.fixed,
      ),
      textTheme: base.textTheme.apply(
        bodyColor: AppColors.textPrimaryLight,
        displayColor: AppColors.textPrimaryLight,
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: AppColors.accent,
          foregroundColor: Colors.white,
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
        ),
      ),
    );
  }
}
