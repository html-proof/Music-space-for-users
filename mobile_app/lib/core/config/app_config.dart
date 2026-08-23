/// Central place for build-time configuration.
///
  // Production traffic goes through the Cloudflare Worker proxy, which
  // forwards requests to https://music-space-for-users.onrender.com.
  // Replace the placeholder below with the actual workers.dev URL shown
  // after running `wrangler deploy` inside cloudflare-worker/.
  //
  // Override at build/run time:
  //   flutter run --dart-define=API_BASE_URL=https://gaanapy-proxy.<account>.workers.dev
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
