/// Mirrors app/schemas/download.py DownloadResponse plus the local-only
/// `localPath` set once the file actually lands on this device.
class DownloadRecord {
  final String id;
  final String songId;
  final String title;
  final String artistName;
  final String? thumbnailUrl;
  final int duration;
  final String deviceId;
  final String status; // queued, downloading, paused, completed, failed
  final String quality;
  final int progressPercent;
  final int? fileSizeBytes;
  final String? audioUrl;
  final String? errorMessage;
  final String? localPath;

  const DownloadRecord({
    required this.id,
    required this.songId,
    required this.title,
    required this.artistName,
    this.thumbnailUrl,
    this.duration = 0,
    required this.deviceId,
    required this.status,
    required this.quality,
    this.progressPercent = 0,
    this.fileSizeBytes,
    this.audioUrl,
    this.errorMessage,
    this.localPath,
  });

  factory DownloadRecord.fromJson(Map<String, dynamic> json, {String? localPath}) {
    return DownloadRecord(
      id: json['id']?.toString() ?? '',
      songId: json['song_id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      artistName: json['artist_name']?.toString() ?? '',
      thumbnailUrl: json['thumbnail_url']?.toString(),
      duration: (json['duration'] as num?)?.toInt() ?? 0,
      deviceId: json['device_id']?.toString() ?? '',
      status: json['status']?.toString() ?? 'queued',
      quality: json['quality']?.toString() ?? 'high_quality',
      progressPercent: (json['progress_percent'] as num?)?.toInt() ?? 0,
      fileSizeBytes: (json['file_size_bytes'] as num?)?.toInt(),
      audioUrl: json['audio_url']?.toString(),
      errorMessage: json['error_message']?.toString(),
      localPath: localPath,
    );
  }

  bool get isCompleted => status == 'completed';
  bool get isFailed => status == 'failed';
  bool get isActive => status == 'queued' || status == 'downloading';

  DownloadRecord copyWith({String? localPath}) {
    return DownloadRecord(
      id: id,
      songId: songId,
      title: title,
      artistName: artistName,
      thumbnailUrl: thumbnailUrl,
      duration: duration,
      deviceId: deviceId,
      status: status,
      quality: quality,
      progressPercent: progressPercent,
      fileSizeBytes: fileSizeBytes,
      audioUrl: audioUrl,
      errorMessage: errorMessage,
      localPath: localPath ?? this.localPath,
    );
  }
}
