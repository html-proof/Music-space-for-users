import '../../../core/network/api_client.dart';
import '../../../shared/models/download.dart';

class DownloadStorageSummary {
  final int totalDownloads;
  final int completedDownloads;
  final int totalBytes;
  final Map<String, int> byStatus;

  const DownloadStorageSummary({
    required this.totalDownloads,
    required this.completedDownloads,
    required this.totalBytes,
    required this.byStatus,
  });

  factory DownloadStorageSummary.fromJson(Map<String, dynamic> json) {
    return DownloadStorageSummary(
      totalDownloads: (json['total_downloads'] as num?)?.toInt() ?? 0,
      completedDownloads: (json['completed_downloads'] as num?)?.toInt() ?? 0,
      totalBytes: (json['total_bytes'] as num?)?.toInt() ?? 0,
      byStatus: (json['by_status'] as Map? ?? const {}).map(
        (key, value) => MapEntry(key.toString(), (value as num).toInt()),
      ),
    );
  }
}

/// Wraps /api/downloads/* (app/api/downloads.py). This only manages the
/// server-side request/queue record; DownloadManager does the actual byte
/// transfer and writes the local file path into LocalDatabase.
class DownloadsRepository {
  DownloadsRepository(this._api);

  final ApiClient _api;

  Future<DownloadRecord> requestDownload({
    required String songId,
    required String deviceId,
    required String quality,
  }) async {
    final data = await _api.post('/api/downloads', body: {
      'song_id': songId,
      'device_id': deviceId,
      'quality': quality,
    });
    return DownloadRecord.fromJson(data as Map<String, dynamic>);
  }

  Future<List<DownloadRecord>> list({String? deviceId, String? status}) async {
    final data = await _api.get('/api/downloads', query: {
      'device_id': deviceId,
      'status': status,
    }) as List;
    return data.map((e) => DownloadRecord.fromJson((e as Map).cast<String, dynamic>())).toList();
  }

  Future<void> updateProgress({
    required String downloadId,
    String? status,
    int? progressPercent,
    int? fileSizeBytes,
    String? errorMessage,
  }) async {
    await _api.patch('/api/downloads/$downloadId', body: {
      if (status != null) 'status': status,
      if (progressPercent != null) 'progress_percent': progressPercent,
      if (fileSizeBytes != null) 'file_size_bytes': fileSizeBytes,
      if (errorMessage != null) 'error_message': errorMessage,
    });
  }

  Future<DownloadStorageSummary> storageSummary({String? deviceId}) async {
    final data = await _api.get('/api/downloads/storage', query: {'device_id': deviceId});
    return DownloadStorageSummary.fromJson(data as Map<String, dynamic>);
  }

  Future<void> deleteDownload(String downloadId) => _api.delete('/api/downloads/$downloadId');

  Future<void> deleteAll({String? deviceId}) => _api.delete('/api/downloads', query: {'device_id': deviceId});
}
