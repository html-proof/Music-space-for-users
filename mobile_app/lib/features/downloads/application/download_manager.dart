import 'dart:async' show unawaited;
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import '../../../core/network/api_client.dart';
import '../../../core/storage/device_id.dart';
import '../../../core/storage/local_database.dart';
import '../../../shared/models/download.dart';
import '../../../shared/models/song.dart';
import '../data/downloads_repository.dart';

/// Drives the permanent-download half of the offline system end to end:
/// request the record from the backend (which hands back a stream URL for
/// the chosen quality), pull the bytes with progress reporting, then persist
/// the resulting file path in LocalDatabase so the player can find it
/// without a network round trip. Deliberately separate from any
/// temporary/transient playback cache -- this is the "stays until the user
/// deletes it" path the spec calls out.
class DownloadManager extends ChangeNotifier {
  DownloadManager(this._repository, this._apiClient, this._localDb);

  final DownloadsRepository _repository;
  final ApiClient _apiClient;
  final LocalDatabase _localDb;

  final Map<String, double> _progress = {};
  final Set<String> _active = {};

  double progressFor(String songId) => _progress[songId] ?? 0;
  bool isActive(String songId) => _active.contains(songId);

  Future<void> download(Song song, {String quality = 'high_quality'}) async {
    if (_active.contains(song.id)) return;
    _active.add(song.id);
    _progress[song.id] = 0;
    notifyListeners();

    try {
      final deviceId = await DeviceIdProvider.get();
      final record = await _repository.requestDownload(
        songId: song.id,
        deviceId: deviceId,
        quality: quality,
      );
      final audioUrl = record.audioUrl;
      if (audioUrl == null || audioUrl.isEmpty) {
        await _repository.updateProgress(
          downloadId: record.id,
          status: 'failed',
          errorMessage: 'No stream URL available for this quality',
        );
        return;
      }

      await _repository.updateProgress(downloadId: record.id, status: 'downloading');

      final dir = await getApplicationDocumentsDirectory();
      final downloadsDir = Directory(p.join(dir.path, 'downloads'));
      if (!await downloadsDir.exists()) {
        await downloadsDir.create(recursive: true);
      }
      final localPath = p.join(downloadsDir.path, '${song.id}.audio');

      int lastReportedPercent = -1;
      await _apiClient.raw.download(
        audioUrl,
        localPath,
        onReceiveProgress: (received, total) {
          if (total <= 0) return;
          final percent = ((received / total) * 100).clamp(0, 100).toInt();
          _progress[song.id] = percent / 100;
          notifyListeners();
          // Throttle backend progress updates to every 10% -- this fires on
          // every chunk otherwise, which is far more write traffic than the
          // queue-state tracking needs.
          if (percent >= lastReportedPercent + 10 || percent == 100) {
            lastReportedPercent = percent;
            unawaited(_repository.updateProgress(downloadId: record.id, progressPercent: percent));
          }
        },
      );

      final file = File(localPath);
      final fileSize = await file.length();

      await _localDb.saveDownloadedFile(
        songId: song.id,
        localPath: localPath,
        fileSize: fileSize,
        quality: quality,
        title: song.title,
        artistName: song.artistName,
        thumbnailUrl: song.thumbnailUrl,
        duration: song.duration,
      );

      await _repository.updateProgress(
        downloadId: record.id,
        status: 'completed',
        fileSizeBytes: fileSize,
      );
    } catch (e) {
      debugPrint('Download failed for ${song.id}: $e');
    } finally {
      _active.remove(song.id);
      _progress.remove(song.id);
      notifyListeners();
    }
  }

  Future<void> removeDownload(DownloadRecord record) async {
    if (record.localPath != null) {
      final file = File(record.localPath!);
      if (await file.exists()) await file.delete();
    }
    await _localDb.removeDownloadedFile(record.songId);
    await _repository.deleteDownload(record.id);
    notifyListeners();
  }

  Future<void> removeAll() async {
    await _localDb.clearAllDownloadedFiles();
    await _repository.deleteAll();
    notifyListeners();
  }
}
