// Android values are real, sourced from android/app/google-services.json
// (project "personal-songs" -- the same Firebase project the backend's
// FIREBASE_PROJECT_ID points at, see render.yaml). iOS is still a
// placeholder: add an iOS app in the Firebase console and re-run
// `flutterfire configure` (or fill in GoogleService-Info.plist's values by
// hand) before building for iOS.
import 'package:firebase_core/firebase_core.dart' show FirebaseOptions;
import 'package:flutter/foundation.dart' show defaultTargetPlatform, kIsWeb, TargetPlatform;

class DefaultFirebaseOptions {
  static FirebaseOptions get currentPlatform {
    if (kIsWeb) {
      throw UnsupportedError(
        'DefaultFirebaseOptions have not been configured for web. '
        'Run `flutterfire configure` to generate real options.',
      );
    }
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return android;
      case TargetPlatform.iOS:
        return ios;
      default:
        throw UnsupportedError(
          'DefaultFirebaseOptions are not supported for this platform. '
          'Run `flutterfire configure` to generate real options.',
        );
    }
  }

  static const FirebaseOptions android = FirebaseOptions(
    apiKey: 'AIzaSyBKWWqGjEr4KZUIqgCW-uXnBdx-Z7wo-Qk',
    appId: '1:404503869615:android:ae7494d0fe31e807dc7c5a',
    messagingSenderId: '404503869615',
    projectId: 'personal-songs',
    storageBucket: 'personal-songs.firebasestorage.app',
  );

  static const FirebaseOptions ios = FirebaseOptions(
    apiKey: 'REPLACE_ME',
    appId: 'REPLACE_ME',
    messagingSenderId: 'REPLACE_ME',
    projectId: 'REPLACE_ME',
    storageBucket: 'REPLACE_ME.appspot.com',
    iosBundleId: 'com.musichub.app',
  );
}
