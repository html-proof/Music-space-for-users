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

  Future<HomeFeed> getHomeFeed() async {
    final data = await _api.get('/api/recommendations/home');
    return HomeFeed.fromJson(data as Map<String, dynamic>);
  }
}
