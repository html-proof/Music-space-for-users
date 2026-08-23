class Artist {
  final String id;
  final String externalId;
  final String name;
  final String? seokey;
  final String? imageUrl;
  final List<String> genres;
  final int songCount;
  final int albumCount;
  final bool isFollowed;

  const Artist({
    required this.id,
    required this.externalId,
    required this.name,
    this.seokey,
    this.imageUrl,
    this.genres = const [],
    this.songCount = 0,
    this.albumCount = 0,
    this.isFollowed = false,
  });

  factory Artist.fromJson(Map<String, dynamic> json) {
    return Artist(
      id: json['id']?.toString() ?? '',
      externalId: json['external_id']?.toString() ?? '',
      name: json['name']?.toString() ?? 'Unknown artist',
      seokey: json['seokey']?.toString(),
      imageUrl: json['image_url']?.toString(),
      genres: (json['genres'] as List?)?.map((e) => e.toString()).toList() ?? const [],
      songCount: (json['song_count'] as num?)?.toInt() ?? 0,
      albumCount: (json['album_count'] as num?)?.toInt() ?? 0,
      isFollowed: json['is_followed'] == true,
    );
  }
}
