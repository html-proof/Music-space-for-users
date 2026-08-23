import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// Local cache of onboarding preferences -- backend is the source of truth,
/// this only exists to (a) survive a transient network failure on cold start
/// and (b) avoid ever needing to hardcode a language/artist list client-side.
///
/// Deliberately not treated as durable storage: it is wiped on uninstall,
/// and sign-out never clears it (the user's preferences live in the backend,
/// not here -- see AuthRepository.signOut, which only touches Firebase).
class LocalPreferencesCache {
  LocalPreferencesCache._();

  static const _languagesKey = 'gaanapy_cached_languages';
  static const _artistsKey = 'gaanapy_cached_artists';
  static const _completedKey = 'gaanapy_cached_onboarding_completed';

  static Future<void> saveLanguages(List<String> languages) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_languagesKey, jsonEncode(languages));
  }

  static Future<List<String>?> getLanguages() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_languagesKey);
    if (raw == null) return null;
    return (jsonDecode(raw) as List).map((e) => e.toString()).toList();
  }

  static Future<void> saveArtists(List<String> artists) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_artistsKey, jsonEncode(artists));
  }

  static Future<List<String>?> getArtists() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_artistsKey);
    if (raw == null) return null;
    return (jsonDecode(raw) as List).map((e) => e.toString()).toList();
  }

  static Future<void> saveOnboardingCompleted(bool completed) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_completedKey, completed);
  }

  static Future<bool?> getOnboardingCompleted() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_completedKey);
  }

  /// Used by Settings' "clear cache" action -- never called on sign-out.
  static Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await Future.wait([
      prefs.remove(_languagesKey),
      prefs.remove(_artistsKey),
      prefs.remove(_completedKey),
    ]);
  }
}
