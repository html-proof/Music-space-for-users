import 'dart:convert';

import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

/// The device-local half of the offline system.
///
/// This is deliberately narrow: song/artist/album *metadata* is served fresh
/// from the REST API (which already sits behind Redis, see
/// app/services/cache_service.py) and rendered straight into the UI --
/// duplicating that cache on-device buys little. What genuinely must live on
/// the device is state the backend cannot know: which audio bytes are
/// actually sitting on this disk, and typed queries against them.
///
/// Tables:
///  - `downloaded_files`: song_id -> local file path + size for completed
///    downloads. The source of truth for "is this playable offline right
///    now" is this table, not the `downloads` status on the server (that
///    tracks the request/queue lifecycle across devices; this tracks bytes
///    on *this* disk).
///  - `recent_searches`: local-first recent search list so it renders
///    instantly before the network round trip to /api/search/history lands.
///  - `pending_actions`: actions taken while offline (like/unlike, playlist
///    edits) queued here and flushed once connectivity returns.
class LocalDatabase {
  LocalDatabase._();
  static final LocalDatabase instance = LocalDatabase._();

  Database? _db;

  Future<Database> get database async {
    _db ??= await _open();
    return _db!;
  }

  Future<Database> _open() async {
    final dir = await getApplicationDocumentsDirectory();
    final path = p.join(dir.path, 'gaanapy_local.db');
    return openDatabase(
      path,
      version: 1,
      onCreate: (db, version) async {
        await db.execute('''
          CREATE TABLE downloaded_files (
            song_id TEXT PRIMARY KEY,
            local_path TEXT NOT NULL,
            file_size INTEGER,
            quality TEXT,
            title TEXT,
            artist_name TEXT,
            thumbnail_url TEXT,
            duration INTEGER,
            downloaded_at TEXT NOT NULL
          )
        ''');
        await db.execute('''
          CREATE TABLE recent_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            searched_at TEXT NOT NULL
          )
        ''');
        await db.execute('''
          CREATE TABLE pending_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
          )
        ''');
      },
    );
  }

  // ---------------------------------------------------------------- files

  Future<void> saveDownloadedFile({
    required String songId,
    required String localPath,
    required int fileSize,
    required String quality,
    required String title,
    required String artistName,
    String? thumbnailUrl,
    int duration = 0,
  }) async {
    final db = await database;
    await db.insert(
      'downloaded_files',
      {
        'song_id': songId,
        'local_path': localPath,
        'file_size': fileSize,
        'quality': quality,
        'title': title,
        'artist_name': artistName,
        'thumbnail_url': thumbnailUrl,
        'duration': duration,
        'downloaded_at': DateTime.now().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<Map<String, dynamic>?> getDownloadedFile(String songId) async {
    final db = await database;
    final rows = await db.query(
      'downloaded_files',
      where: 'song_id = ?',
      whereArgs: [songId],
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first;
  }

  Future<bool> isDownloaded(String songId) async {
    final row = await getDownloadedFile(songId);
    return row != null;
  }

  Future<List<Map<String, dynamic>>> allDownloadedFiles() async {
    final db = await database;
    return db.query('downloaded_files', orderBy: 'downloaded_at DESC');
  }

  Future<void> removeDownloadedFile(String songId) async {
    final db = await database;
    await db.delete('downloaded_files', where: 'song_id = ?', whereArgs: [songId]);
  }

  Future<void> clearAllDownloadedFiles() async {
    final db = await database;
    await db.delete('downloaded_files');
  }

  Future<int> totalDownloadedBytes() async {
    final db = await database;
    final result = await db.rawQuery('SELECT COALESCE(SUM(file_size), 0) AS total FROM downloaded_files');
    return Sqflite.firstIntValue(result) ?? 0;
  }

  // ---------------------------------------------------------------- search

  Future<void> addRecentSearch(String query) async {
    if (query.trim().isEmpty) return;
    final db = await database;
    await db.delete('recent_searches', where: 'query = ?', whereArgs: [query]);
    await db.insert('recent_searches', {
      'query': query,
      'searched_at': DateTime.now().toIso8601String(),
    });
    final rows = await db.query('recent_searches', orderBy: 'searched_at DESC');
    if (rows.length > 20) {
      for (final row in rows.skip(20)) {
        await db.delete('recent_searches', where: 'id = ?', whereArgs: [row['id']]);
      }
    }
  }

  Future<List<String>> recentSearches({int limit = 10}) async {
    final db = await database;
    final rows = await db.query('recent_searches', orderBy: 'searched_at DESC', limit: limit);
    return rows.map((r) => r['query'] as String).toList();
  }

  Future<void> clearRecentSearches() async {
    final db = await database;
    await db.delete('recent_searches');
  }

  // -------------------------------------------------------------- offline sync

  Future<void> queuePendingAction(String actionType, Map<String, dynamic> payload) async {
    final db = await database;
    await db.insert('pending_actions', {
      'action_type': actionType,
      'payload': jsonEncode(payload),
      'created_at': DateTime.now().toIso8601String(),
    });
  }

  Future<List<Map<String, dynamic>>> pendingActions() async {
    final db = await database;
    final rows = await db.query('pending_actions', orderBy: 'created_at ASC');
    return rows
        .map((r) => {
              'id': r['id'],
              'action_type': r['action_type'],
              'payload': jsonDecode(r['payload'] as String),
            })
        .toList();
  }

  Future<void> removePendingAction(int id) async {
    final db = await database;
    await db.delete('pending_actions', where: 'id = ?', whereArgs: [id]);
  }
}
