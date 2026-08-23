import 'package:firebase_auth/firebase_auth.dart';
import 'package:google_sign_in/google_sign_in.dart';

/// The client half of "Google Sign-In only". There is no email/password
/// path anywhere in this app -- the backend also independently rejects any
/// Firebase token whose sign_in_provider isn't google.com (see
/// app/middleware/firebase_auth.py), so this is defense in depth, not the
/// only guard.
class GoogleAuthService {
  GoogleAuthService({FirebaseAuth? firebaseAuth, GoogleSignIn? googleSignIn})
      : _firebaseAuth = firebaseAuth ?? FirebaseAuth.instance,
        _googleSignIn = googleSignIn ?? GoogleSignIn(scopes: const ['email', 'profile']);

  final FirebaseAuth _firebaseAuth;
  final GoogleSignIn _googleSignIn;

  User? get currentUser => _firebaseAuth.currentUser;
  Stream<User?> get authStateChanges => _firebaseAuth.authStateChanges();

  Future<User> signInWithGoogle() async {
    final account = await _googleSignIn.signIn();
    if (account == null) {
      // User dismissed the account picker.
      throw StateError('Google sign-in was cancelled.');
    }
    final authentication = await account.authentication;
    final credential = GoogleAuthProvider.credential(
      idToken: authentication.idToken,
      accessToken: authentication.accessToken,
    );
    final result = await _firebaseAuth.signInWithCredential(credential);
    final user = result.user;
    if (user == null) {
      throw StateError('Google sign-in did not return a Firebase user.');
    }
    return user;
  }

  Future<void> signOut() async {
    await Future.wait([
      _firebaseAuth.signOut(),
      _googleSignIn.signOut(),
    ]);
  }
}
