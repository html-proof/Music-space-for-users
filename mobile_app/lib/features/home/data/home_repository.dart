import '../../../core/network/api_client.dart';
import '../../../shared/models/song.dart';

class HomeCategory {
  final String id;
  final String title;
  final String? description;
  final List<Song> items;

  const HomeCategory({required this.id, required this.title, this.description, required this.items});

  factory HomeCategory.fromJson(Map<String, dynamic> json) {
    return HomeCategory(
      id: json['id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      description: json['description']?.toString(),
      items: (json['items'] as List? ?? const [])
          .map((e) => Song.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
    );
  }
}

class HomeFeed {
  final String greeting;
  final List<Song> topMix;
  final List<HomeCategory> categories;

  const HomeFeed({required this.greeting, required this.topMix, required this.categories});

  factory HomeFeed.fromJson(Map<String, dynamic> json) {
    return HomeFeed(
      greeting: json['greeting']?.toString() ?? 'Welcome back',
      topMix: (json['top_mix'] as List? ?? const [])
          .map((e) => Song.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
      categories: (json['categories'] as List? ?? const [])
          .map((e) => HomeCategory.fromJson((e as Map).cast<String, dynamic>()))
          .toList(),
    );
  }
}

/// Wraps GET /api/recommendations/home (app/api/recommendations.py).
class HomeRepository {
  HomeRepository(this._api);

  final ApiClient _api;

  /// `refresh: true` bypasses the server's cached personalized ranking and
  /// recomputes it against the database as it is right now -- without this,
  /// pull-to-refresh only invalidated the client's own cache and the server
  /// kept serving the same up-to-an-hour-old snapshot underneath it.
  Future<HomeFeed> getHomeFeed({bool refresh = false}) async {
    final data = await _api.get(
      '/api/recommendations/home',
      query: refresh ? {'refresh': true} : null,
    );
    return HomeFeed.fromJson(data as Map<String, dynamic>);
  }
}
