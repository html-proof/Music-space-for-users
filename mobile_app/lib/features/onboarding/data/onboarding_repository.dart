import '../../../core/network/api_client.dart';
import '../../../shared/models/artist.dart';
import '../../../shared/models/language.dart';

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

/// Wraps /api/onboarding/* (app/api/onboarding.py). The backend is the
/// source of truth for every value here -- languages and suggested artists
/// come from GET endpoints backed by real catalog tables, never a
/// client-side hardcoded list.
class OnboardingRepository {
  OnboardingRepository(this._api);

  final ApiClient _api;

  Future<List<LanguageOption>> getLanguages() async {
    final data = await _api.get('/api/onboarding/languages') as List;
    return data.map((e) => LanguageOption.fromJson((e as Map).cast<String, dynamic>())).toList();
  }

  Future<List<Artist>> getSuggestedArtists({int limit = 30}) async {
    final data = await _api.get('/api/onboarding/artists/suggestions', query: {'limit': limit}) as List;
    return data.map((e) => Artist.fromJson((e as Map).cast<String, dynamic>())).toList();
  }

  Future<OnboardingStatus> getStatus() async {
    final data = await _api.get('/api/onboarding/status');
    return OnboardingStatus.fromJson(data as Map<String, dynamic>);
  }

  Future<OnboardingStatus> setLanguages(List<String> languages) async {
    final data = await _api.post('/api/onboarding/languages', body: {'languages': languages});
    return OnboardingStatus.fromJson(data as Map<String, dynamic>);
  }

  /// Most suggested artists are never persisted server-side (see
  /// app/services/onboarding_service.py) -- only the ones the user actually
  /// picks, so the full artist (not just an id) has to travel with the
  /// selection for the backend to upsert it on the spot.
  Future<OnboardingStatus> setArtists(List<Artist> artists) async {
    final data = await _api.post('/api/onboarding/artists', body: {
      'artists': artists
          .map((a) => {'id': a.id, 'name': a.name, 'image_url': a.imageUrl, 'seokey': a.seokey})
          .toList(),
    });
    return OnboardingStatus.fromJson(data as Map<String, dynamic>);
  }

  Future<OnboardingStatus> complete() async {
    final data = await _api.post('/api/onboarding/complete');
    return OnboardingStatus.fromJson(data as Map<String, dynamic>);
  }
}
