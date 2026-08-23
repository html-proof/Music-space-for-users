/// Mirrors GET /api/auth/me.
class AppUser {
  final String id;
  final String firebaseUid;
  final String? email;
  final String? displayName;
  final String? photoUrl;
  final String? country;
  final String? language;

  const AppUser({
    required this.id,
    required this.firebaseUid,
    this.email,
    this.displayName,
    this.photoUrl,
    this.country,
    this.language,
  });

  factory AppUser.fromJson(Map<String, dynamic> json) {
    return AppUser(
      id: json['id']?.toString() ?? '',
      firebaseUid: json['firebase_uid']?.toString() ?? '',
      email: json['email']?.toString(),
      displayName: json['display_name']?.toString(),
      photoUrl: json['photo_url']?.toString(),
      country: json['country']?.toString(),
      language: json['language']?.toString(),
    );
  }
}
