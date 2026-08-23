# GaanaPy Flutter App

Flutter client for the GaanaPy backend (`../app`). Feature-based architecture,
Riverpod state management, go_router navigation, Google Sign-In only.

## Required setup before running

1. **Firebase project.** Create one at https://console.firebase.google.com,
   enable **Authentication → Sign-in method → Google** only (leave
   Email/Password disabled — the backend already rejects any other
   provider's tokens, see `app/middleware/firebase_auth.py`).
2. **Generate real Firebase config.**
   ```
   dart pub global activate flutterfire_cli
   cd mobile_app
   flutterfire configure
   ```
   This overwrites the placeholder `lib/firebase_options.dart` with your
   project's real values and wires up `android/app/google-services.json` /
   `ios/Runner/GoogleService-Info.plist` as needed.
3. **Point the app at your backend.** Defaults to
   `http://10.0.2.2:8000` (Android emulator's alias for the host machine's
   `localhost`, matching `uvicorn app.main:app` running locally). Override
   with:
   ```
   flutter run --dart-define=API_BASE_URL=https://your-backend.example.com
   ```
   iOS simulators and physical devices need an explicit `API_BASE_URL`.
4. **Backend must have `FIREBASE_PROJECT_ID`/`FIREBASE_CLIENT_EMAIL`/
   `FIREBASE_PRIVATE_KEY`** (or `FIREBASE_CREDENTIALS_PATH`) set to the same
   Firebase project, so it can verify the ID tokens this app sends.

## Running

```
flutter pub get
flutter run
```

## Structure

```
lib/
├── core/          # config, network client, local db, theme, router
├── features/      # one folder per feature: data/ (repositories),
│                  # application/ (riverpod providers), presentation/ (UI)
├── shared/        # models and widgets used across features
└── main.dart
```

## What's implemented

Google Sign-In auth, onboarding (languages/artists), home feed, search,
full player (queue/shuffle/repeat/seek) backed by just_audio, artist/album/
playlist/library screens, lyrics (plain + time-synced), permanent downloads
with progress tracking synced to `/api/downloads`, and settings (theme,
audio quality, playback prefs, storage, account).

## Known gaps / next steps

- **Background/lock-screen playback** (`audio_service`/media-session
  integration) is not wired in — playback currently only continues while the
  app is foregrounded or backgrounded but not killed by the OS. Adding it
  needs a real device to verify notification/lock-screen controls.
- **Temporary/transient playback cache** (auto-evicting LRU cache distinct
  from permanent downloads) is not implemented — only the explicit
  "Download" path exists. just_audio does its own network buffering, but
  there's no on-disk temp cache with size-based eviction yet.
- **Offline write queue** (queue liked/playlist actions made while offline,
  flush on reconnect) has a `pending_actions` table in `LocalDatabase` ready
  to use, but nothing enqueues into it yet — all actions currently require
  a live connection.
- Artist/album detail pages call `/api/catalog/artists|albums/info`, which
  proxies raw Gaana data and does **not** persist a followable/saveable
  artist row when reached that way (only artists/albums seen via search are
  upserted with an internal id). Following/saving from a detail page reached
  via "similar artists" may show a "not found" toast rather than a real
  gap in most flows (search, library, home) but is a known limitation worth
  a dedicated backend endpoint (`GET /api/artists/{id}`, `GET /api/albums/{id}`)
  if it comes up in testing.
