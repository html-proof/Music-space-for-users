import 'song.dart';

class PlaylistSongItem {
  final String id;
  final String playlistId;
  final String songId;
  final int position;
  final Song? song;

  const PlaylistSongItem({
    required this.id,
    required this.playlistId,
    required this.songId,
    required this.position,
    this.song,
  });

  factory PlaylistSongItem.fromJson(Map<String, dynamic> json) {
    return PlaylistSongItem(
      id: json['id']?.toString() ?? '',
      playlistId: json['playlist_id']?.toString() ?? '',
      songId: json['song_id']?.toString() ?? '',
      position: (json['position'] as num?)?.toInt() ?? 0,
      song: json['song'] is Map
          ? Song.fromJson((json['song'] as Map).cast<String, dynamic>())
          : null,
    );
  }
}

class Playlist {
  final String id;
  final String userId;
  final String title;
  final String? description;
  final bool isPublic;
  final bool isCollaborative;
  final String? coverUrl;
  final int songCount;
  final List<PlaylistSongItem> songs;

  const Playlist({
    required this.id,
    required this.userId,
    required this.title,
    this.description,
    this.isPublic = false,
    this.isCollaborative = false,
    this.coverUrl,
    this.songCount = 0,
    this.songs = const [],
  });

  factory Playlist.fromJson(Map<String, dynamic> json) {
    return Playlist(
      id: json['id']?.toString() ?? '',
      userId: json['user_id']?.toString() ?? '',
      title: json['title']?.toString() ?? 'Untitled playlist',
      description: json['description']?.toString(),
      isPublic: json['is_public'] == true,
      isCollaborative: json['is_collaborative'] == true,
      coverUrl: json['cover_url']?.toString(),
      songCount: (json['song_count'] as num?)?.toInt() ?? 0,
      songs: (json['songs'] as List?)
              ?.map((e) => PlaylistSongItem.fromJson((e as Map).cast<String, dynamic>()))
              .toList() ??
          const [],
    );
  }
}
