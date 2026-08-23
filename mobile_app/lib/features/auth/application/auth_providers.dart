import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../../../shared/models/app_user.dart';
import '../data/auth_repository.dart';
import '../data/google_auth_service.dart';

final googleAuthServiceProvider = Provider<GoogleAuthService>((ref) => GoogleAuthService());

final authRepositoryProvider = Provider<AuthRepository>((ref) {
  return AuthRepository(ref.watch(apiClientProvider), ref.watch(googleAuthServiceProvider));
});

/// Raw Firebase sign-in state. Flips to null the instant sign-out happens
/// (locally, or via ApiClient's onUnauthorized callback after a 401).
final firebaseUserProvider = StreamProvider<User?>((ref) {
  return ref.watch(authRepositoryProvider).authStateChanges;
});

/// The backend `users` row for whoever is currently signed in, re-synced
/// every time Firebase's auth state changes. Null while signed out.
final currentUserProvider = FutureProvider<AppUser?>((ref) async {
  // `.future`, not `ref.watch(firebaseUserProvider).value`: the latter reads
  // whatever the StreamProvider's AsyncValue holds *right now*, which on a
  // cold start is `loading` (value == null) because authStateChanges()
  // hasn't emitted its first event yet -- Firebase needs a tick to restore
  // any persisted session. Reading `.value` there raced that restore and
  // read "signed out" even for an already-logged-in user. `.future` awaits
  // the stream's first real emission instead of guessing at a snapshot.
  final firebaseUser = await ref.watch(firebaseUserProvider.future);
  if (firebaseUser == null) return null;
  final repo = ref.watch(authRepositoryProvider);
  return repo.syncProfile();
});
