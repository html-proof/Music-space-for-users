import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme/app_theme.dart';
import '../models/song.dart';

/// Secondary actions for one song, shown as a bottom sheet.
///
/// Exists so "open the album this song is on" is reachable from every list a
/// song appears in -- home, search, library, playlists, artist pages -- without
/// each of those screens growing its own menu.
///
/// Navigation uses `song.albumId`, which is our own uuid rather than a Gaana
/// seokey. That is deliberate: the uuid is the only album identifier a song
/// payload carries, and the backend resolves it (see
/// `catalog_service.resolve_album_seokey`). It also happens to be the id the
/// album screen's save button needs, which a raw Gaana key is not.
class SongActionsSheet extends StatelessWidget {
  const SongActionsSheet({super.key, required this.song});

  final Song song;

  /// Whether there is anything worth showing. A song with no album reference
  /// has nothing here, so callers can skip opening an empty sheet.
  static bool hasActions(Song song) => _canOpenAlbum(song);

  static bool _canOpenAlbum(Song song) =>
      (song.albumId ?? '').isNotEmpty && song.albumName.trim().isNotEmpty;

  static Future<void> show(BuildContext context, Song song) {
    return showModalBottomSheet<void>(
      context: context,
      backgroundColor: AppColors.surfaceRaised,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (_) => SongActionsSheet(song: song),
    );
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const SizedBox(height: 8),
          Container(
            width: 36,
            height: 4,
            decoration: BoxDecoration(
              color: AppColors.textSecondary,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          const SizedBox(height: 12),
          ListTile(
            title: Text(
              song.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            subtitle: Text(
              song.artistName,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: AppColors.textSecondary),
            ),
          ),
          const Divider(height: 1),
          if (_canOpenAlbum(song))
            ListTile(
              leading: const Icon(Icons.album_outlined),
              title: const Text('Go to album'),
              subtitle: Text(
                song.albumName,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: AppColors.textSecondary),
              ),
              onTap: () {
                // Pop first so the sheet is not left under the pushed route.
                Navigator.of(context).pop();
                context.push('/album/${song.albumId}');
              },
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}
