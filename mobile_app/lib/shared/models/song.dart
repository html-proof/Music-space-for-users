/// Mirrors the song dict shape shared by catalog/search/recommendations/
/// library/playlists endpoints (see the `_format_song` helpers in
/// app/api/*.py -- they are intentionally identical across routers).
class Song {
  final String id;
  final String externalId;
  final String title;
  final String? artistId;
  final String artistName;
  final String? albumId;
  final String albumName;
  final int duration;
  final String? thumbnailUrl;
  final String? audioUrl;
  final Map<String, dynamic> streamUrls;
  final String? language;
  final String? genre;
  final String? mood;
  final bool isExplicit;
  final int playCount;
  final bool isLiked;

  const Song({
    required this.id,
    required this.externalId,
    required this.title,
    this.artistId,
    this.artistName = '',
    this.albumId,
    this.albumName = '',
    this.duration = 0,
    this.thumbnailUrl,
    this.audioUrl,
    this.streamUrls = const {},
    this.language,
    this.genre,
    this.mood,
    this.isExplicit = false,
    this.playCount = 0,
    this.isLiked = false,
  });

  factory Song.fromJson(Map<String, dynamic> json) {
    return Song(
      id: json['id']?.toString() ?? '',
      externalId: json['external_id']?.toString() ?? '',
      title: json['title']?.toString() ?? 'Unknown title',
      artistId: json['artist_id']?.toString(),
      artistName: json['artist_name']?.toString() ?? '',
      albumId: json['album_id']?.toString(),
      albumName: json['album_name']?.toString() ?? '',
      duration: (json['duration'] as num?)?.toInt() ?? 0,
      thumbnailUrl: json['thumbnail_url']?.toString(),
      audioUrl: json['audio_url']?.toString(),
      streamUrls: (json['stream_urls'] as Map?)?.cast<String, dynamic>() ?? const {},
      language: json['language']?.toString(),
      genre: json['genre']?.toString(),
      mood: json['mood']?.toString(),
      isExplicit: json['is_explicit'] == true,
      playCount: (json['play_count'] as num?)?.toInt() ?? 0,
      isLiked: json['is_liked'] == true,
    );
  }

  /// Parses the raw Gaana-formatted track shape returned by
  /// /api/catalog/artists/info (`top_tracks`) and /api/catalog/albums/info
  /// (`tracks`) -- these are pre-normalized by the GaanaPy client's
  /// `format_json_songs`, but with different field names/nesting than our
  /// own catalog/search/recommendations endpoints (`artists` not
  /// `artist_name`, nested `images.urls.*` not a flat `thumbnail_url`, etc).
  /// The seokey doubles as `id`: every backend endpoint that takes a song id
  /// also accepts a Gaana seokey and upserts it into our catalog on first
  /// touch (see CatalogService.get_song_by_id), so this song is fully usable
  /// -- liking, downloading, playing -- before it has ever been persisted.
  factory Song.fromGaanaRaw(Map<String, dynamic> json) {
    final seokey = json['seokey']?.toString() ?? '';
    final images = (json['images'] as Map?)?['urls'] as Map? ?? const {};
    final streams = (json['stream_urls'] as Map?)?['urls'] as Map? ?? const {};
    int parseDuration(dynamic value) {
      if (value == null) return 0;
      return int.tryParse(value.toString()) ?? 0;
    }

    return Song(
      id: seokey,
      externalId: seokey,
      title: json['title']?.toString() ?? 'Unknown title',
      artistName: json['artists']?.toString() ?? '',
      albumName: json['album']?.toString() ?? '',
      duration: parseDuration(json['duration']),
      thumbnailUrl: (images['large_artwork'] ?? images['medium_artwork'])?.toString(),
      audioUrl: (streams['very_high_quality'] ?? streams['high_quality'])?.toString(),
      streamUrls: streams.cast<String, dynamic>(),
      language: json['language']?.toString(),
      genre: json['genres']?.toString(),
      isExplicit: json['is_explicit'] == true,
    );
  }

  /// Best playable URL for `quality` (one of AppConfig.audioQualities),
  /// falling back to whatever the catalog upsert last stored.
  String? streamUrlFor(String quality) {
    return streamUrls[quality] as String? ?? audioUrl;
  }

  String get durationLabel {
    final minutes = duration ~/ 60;
    final seconds = duration % 60;
    return '$minutes:${seconds.toString().padLeft(2, '0')}';
  }

  Song copyWith({bool? isLiked}) {
    return Song(
      id: id,
      externalId: externalId,
      title: title,
      artistId: artistId,
      artistName: artistName,
      albumId: albumId,
      albumName: albumName,
      duration: duration,
      thumbnailUrl: thumbnailUrl,
      audioUrl: audioUrl,
      streamUrls: streamUrls,
      language: language,
      genre: genre,
      mood: mood,
      isExplicit: isExplicit,
      playCount: playCount,
      isLiked: isLiked ?? this.isLiked,
    );
  }
}
