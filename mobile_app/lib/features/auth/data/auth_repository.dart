import 'package:firebase_auth/firebase_auth.dart';

import '../../../core/network/api_client.dart';
import '../../../shared/models/app_user.dart';
import 'google_auth_service.dart';

/// Bridges Firebase identity to the backend's `users` row.
///
/// The Firebase UID is never sent as a plain field the client controls --
/// the backend derives it itself from the verified ID token attached by
/// ApiClient (see app/middleware/firebase_auth.py). `syncProfile` exists
/// only to let the client hand over display info Firebase already knows
/// (name/photo/email) on first login and after profile edits.
class AuthRepository {
  AuthRepository(this._api, this._googleAuth);

  final ApiClient _api;
  final GoogleAuthService _googleAuth;

  Stream<User?> get authStateChanges => _googleAuth.authStateChanges;
  User? get firebaseUser => _googleAuth.currentUser;

  Future<User> signInWithGoogle() => _googleAuth.signInWithGoogle();

  Future<void> signOut() => _googleAuth.signOut();

  Future<AppUser> syncProfile() async {
    final user = _googleAuth.currentUser;
    final data = await _api.post('/api/auth/sync', body: {
      if (user?.displayName != null) 'display_name': user!.displayName,
      if (user?.photoURL != null) 'photo_url': user!.photoURL,
      if (user?.email != null) 'email': user!.email,
    });
    return AppUser.fromJson(data as Map<String, dynamic>);
  }

  Future<AppUser> getCurrentUser() async {
    final data = await _api.get('/api/auth/me');
    return AppUser.fromJson(data as Map<String, dynamic>);
  }

  Future<void> deleteAccount() async {
    await _api.delete('/api/auth/account');
    await signOut();
  }
}
