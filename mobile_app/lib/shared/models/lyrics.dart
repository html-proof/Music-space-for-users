class SyncedLyricLine {
  final int timeMs;
  final String text;

  const SyncedLyricLine({required this.timeMs, required this.text});

  factory SyncedLyricLine.fromJson(Map<String, dynamic> json) {
    return SyncedLyricLine(
      timeMs: (json['time_ms'] as num?)?.toInt() ?? 0,
      text: json['text']?.toString() ?? '',
    );
  }
}

/// Mirrors app/schemas/lyrics.py LyricsResponse.
class LyricsData {
  final String songId;
  final bool hasLyrics;
  final bool isSynced;
  final String? plainText;
  final List<SyncedLyricLine> syncedLines;
  final String? language;

  const LyricsData({
    required this.songId,
    required this.hasLyrics,
    required this.isSynced,
    this.plainText,
    this.syncedLines = const [],
    this.language,
  });

  factory LyricsData.fromJson(Map<String, dynamic> json) {
    return LyricsData(
      songId: json['song_id']?.toString() ?? '',
      hasLyrics: json['has_lyrics'] == true,
      isSynced: json['is_synced'] == true,
      plainText: json['plain_text']?.toString(),
      syncedLines: (json['synced_lines'] as List?)
              ?.map((e) => SyncedLyricLine.fromJson((e as Map).cast<String, dynamic>()))
              .toList() ??
          const [],
      language: json['language']?.toString(),
    );
  }
}
