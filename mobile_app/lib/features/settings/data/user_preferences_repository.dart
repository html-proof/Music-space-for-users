import '../../../core/network/api_client.dart';
import '../../../shared/models/user_preferences.dart';

/// Wraps GET/PATCH /api/users/preferences (app/api/users.py).
class UserPreferencesRepository {
  UserPreferencesRepository(this._api);

  final ApiClient _api;

  Future<UserPreferencesData> get() async {
    final data = await _api.get('/api/users/preferences');
    return UserPreferencesData.fromJson(data as Map<String, dynamic>);
  }

  Future<UserPreferencesData> update(Map<String, dynamic> patch) async {
    final data = await _api.patch('/api/users/preferences', body: patch);
    return UserPreferencesData.fromJson(data as Map<String, dynamic>);
  }
}
