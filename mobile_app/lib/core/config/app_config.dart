/// Central place for build-time configuration.
///
/// Override at build/run time with:
///   flutter run --dart-define=API_BASE_URL=https://your-backend.example.com
///
/// Defaults to the Android emulator's loopback alias for a locally running
/// `uvicorn app.main:app` (10.0.2.2 -> host machine's 127.0.0.1). iOS
/// simulators and physical devices need an explicit override.
class AppConfig {
  AppConfig._();

  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000',
  );

  static const String appName = 'GaanaPy';

  /// Matches app/services/download_service.py DOWNLOAD_QUALITIES.
  static const List<String> audioQualities = [
    'low_quality',
    'medium_quality',
    'high_quality',
    'very_high_quality',
  ];

  // Selectable languages and onboarding artist suggestions are deliberately
  // not listed here -- they come from GET /api/onboarding/languages and
  // GET /api/onboarding/artists/suggestions so the backend catalog stays the
  // single source of truth (see features/onboarding/data/onboarding_repository.dart).
}
