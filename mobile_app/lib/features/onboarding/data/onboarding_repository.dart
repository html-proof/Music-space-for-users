import '../../../core/network/api_client.dart';

class OnboardingStatus {
  final bool completed;
  final List<String> preferredLanguages;
  final List<String> favoriteArtists;

  const OnboardingStatus({
    required this.completed,
    required this.preferredLanguages,
    required this.favoriteArtists,
  });

  factory OnboardingStatus.fromJson(Map<String, dynamic> json) {
    return OnboardingStatus(
      completed: json['completed'] == true,
      preferredLanguages:
          (json['preferred_languages'] as List?)?.map((e) => e.toString()).toList() ?? const [],
      favoriteArtists:
          (json['favorite_artists'] as List?)?.map((e) => e.toString()).toList() ?? const [],
    );
  }
}

/// Wraps /api/onboarding/* (app/api/onboarding.py).
class OnboardingRepository {
  OnboardingRepository(this._api);

  final ApiClient _api;

  Future<OnboardingStatus> getStatus() async {
    final data = await _api.get('/api/onboarding/status');
    return OnboardingStatus.fromJson(data as Map<String, dynamic>);
  }

  Future<OnboardingStatus> setLanguages(List<String> languages) async {
    final data = await _api.post('/api/onboarding/languages', body: {'languages': languages});
    return OnboardingStatus.fromJson(data as Map<String, dynamic>);
  }

  Future<OnboardingStatus> setArtists(List<String> artistIds) async {
    final data = await _api.post('/api/onboarding/artists', body: {'artist_ids': artistIds});
    return OnboardingStatus.fromJson(data as Map<String, dynamic>);
  }

  Future<OnboardingStatus> complete() async {
    final data = await _api.post('/api/onboarding/complete');
    return OnboardingStatus.fromJson(data as Map<String, dynamic>);
  }
}
