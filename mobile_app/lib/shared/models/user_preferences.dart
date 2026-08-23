/// Mirrors GET/PATCH /api/users/preferences (app/schemas/user.py).
class UserPreferencesData {
  final List<String> preferredLanguages;
  final List<String> favoriteArtists;
  final List<String> favoriteGenres;
  final List<String> favoriteMoods;
  final bool explicitContent;
  final String audioQuality;
  final bool autoplay;
  final int crossfade;
  final bool dataSaver;

  const UserPreferencesData({
    this.preferredLanguages = const [],
    this.favoriteArtists = const [],
    this.favoriteGenres = const [],
    this.favoriteMoods = const [],
    this.explicitContent = true,
    this.audioQuality = 'very_high',
    this.autoplay = true,
    this.crossfade = 0,
    this.dataSaver = false,
  });

  factory UserPreferencesData.fromJson(Map<String, dynamic> json) {
    List<String> asStringList(dynamic value) =>
        (value as List?)?.map((e) => e.toString()).toList() ?? const [];
    return UserPreferencesData(
      preferredLanguages: asStringList(json['preferred_languages']),
      favoriteArtists: asStringList(json['favorite_artists']),
      favoriteGenres: asStringList(json['favorite_genres']),
      favoriteMoods: asStringList(json['favorite_moods']),
      explicitContent: json['explicit_content'] != false,
      audioQuality: json['audio_quality']?.toString() ?? 'very_high',
      autoplay: json['autoplay'] != false,
      crossfade: (json['crossfade'] as num?)?.toInt() ?? 0,
      dataSaver: json['data_saver'] == true,
    );
  }
}
