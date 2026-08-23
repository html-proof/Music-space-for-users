import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../network/api_client.dart';
import '../storage/local_database.dart';

/// Single shared Dio-backed client. `onUnauthorized` signs the user out
/// locally when the backend rejects the current Firebase token (expired,
/// revoked, or -- per the Google-only auth policy -- issued by a disallowed
/// provider) so the router's auth redirect takes over immediately instead of
/// the app sitting on a screen that will 401 on every request.
final apiClientProvider = Provider<ApiClient>((ref) {
  return ApiClient(onUnauthorized: () => FirebaseAuth.instance.signOut());
});

final localDatabaseProvider = Provider<LocalDatabase>((ref) => LocalDatabase.instance);
