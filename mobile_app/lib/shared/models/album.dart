import 'song.dart';

class Album {
  final String id;
  final String externalId;
  final String title;
  final String? seokey;
  final String? artistId;
  final String artistName;
  final String? coverUrl;
  final String? releaseDate;
  final String? language;
  final int trackCount;
  final bool isSaved;
  final List<Song>? tracks;

  const Album({
    required this.id,
    required this.externalId,
    required this.title,
    this.seokey,
    this.artistId,
    this.artistName = '',
    this.coverUrl,
    this.releaseDate,
    this.language,
    this.trackCount = 0,
    this.isSaved = false,
    this.tracks,
  });

  factory Album.fromJson(Map<String, dynamic> json) {
    return Album(
      id: json['id']?.toString() ?? '',
      externalId: json['external_id']?.toString() ?? '',
      title: json['title']?.toString() ?? 'Unknown album',
      seokey: json['seokey']?.toString(),
      artistId: json['artist_id']?.toString(),
      artistName: json['artist_name']?.toString() ?? '',
      coverUrl: json['cover_url']?.toString(),
      releaseDate: json['release_date']?.toString(),
      language: json['language']?.toString(),
      trackCount: (json['track_count'] as num?)?.toInt() ?? 0,
      isSaved: json['is_saved'] == true,
      tracks: (json['tracks'] as List?)
          ?.map((e) => Song.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}
