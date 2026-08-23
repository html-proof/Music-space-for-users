import 'package:flutter/material.dart';

/// A clean, minimal light palette: white/near-white surfaces, near-black
/// primary text, mid-gray secondary text, and a single blue spot-accent for
/// interactive/highlighted states -- matching the reference design's mostly
/// monochrome look. Primary CTA chrome (play buttons) is plain black/white,
/// handled directly by PrimaryPlayButton/ArtworkPlayOverlay rather than this
/// accent, since the reference uses solid black or white circles for those
/// regardless of the surrounding theme.
///
/// Most widgets in this app reference these constants directly rather than
/// through Theme.of(context), so changing the values here re-skins the whole
/// app; see AppTheme.light/.dark for the Material-level wiring used by
/// widgets that do read the theme (buttons, sliders, chips).
class AppColors {
  AppColors._();

  static const accent = Color(0xFF4A9DFF);
  static const accentMuted = Color(0xFF2E6FBD);
  static const background = Color(0xFFFFFFFF);
  static const surface = Color(0xFFF7F7F9);
  static const surfaceRaised = Color(0xFFEFEFF3);
  static const textPrimary = Color(0xFF16161A);
  static const textSecondary = Color(0xFF8A8A94);
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

  // AppTheme.light's Material-level colors. Kept equal to the constants
  // above rather than a separate palette: most widgets in this app read
  // AppColors.* directly (not Theme.of(context)), so a genuinely different
  // dark-mode palette here would make Settings' theme toggle produce a
  // visibly broken mixed-light-and-dark screen instead of an actual dark
  // mode. Both AppTheme variants render the same look until that's fixed.
  static const backgroundLight = background;
  static const surfaceLight = surface;
  static const surfaceRaisedLight = surfaceRaised;
  static const textPrimaryLight = textPrimary;
  static const textSecondaryLight = textSecondary;
}

class AppTheme {
  AppTheme._();

  static ThemeData get dark {
    // Both AppTheme variants render the same light look (see the AppColors
    // doc comment above), so this starts from ThemeData.light() too --
    // ThemeData.dark()'s default icon/selection colors are tuned for a dark
    // brightness and would risk near-invisible chrome anywhere this theme's
    // explicit overrides below don't reach.
    final base = ThemeData.light(useMaterial3: true);
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
