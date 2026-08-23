/// Central place for build-time configuration.
///
/// Override at build/run time with:
///   flutter run --dart-define=API_BASE_URL=https://your-backend.example.com
///
/// Defaults to the Cloudflare Worker proxy in front of the Render backend
/// (see ../../../../cloudflare-worker/src/index.js) -- what every real build
/// (a release APK installed on an actual device, or any environment that
/// isn't a local emulator pointed at a `uvicorn` process on the same dev
/// machine) needs. Override to `http://10.0.2.2:8000` explicitly for that
/// emulator-against-localhost workflow instead.
class AppConfig {
  AppConfig._();

  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'https://gaanapy-proxy.imeseban.workers.dev',
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
