import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/theme/app_theme.dart';
import '../../features/player/application/player_providers.dart';
import '../models/song.dart';

class SongTile extends ConsumerWidget {
  const SongTile({
    super.key,
    required this.song,
    required this.onTap,
    this.trailing,
    this.dense = false,
    this.index,
  });

  final Song song;
  final VoidCallback onTap;
  final Widget? trailing;
  final bool dense;

  /// When set, shows a 1-based track number instead of the artwork
  /// thumbnail, and a default cloud/drag-handle trailing row (unless
  /// [trailing] is explicitly provided) -- matches the reference design's
  /// numbered album/playlist track list rows.
  final int? index;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final player = ref.watch(playerControllerProvider);
    // Tapping a song should visibly become "now playing", not just silently
    // start audio -- this is what actually renders that state in every list
    // the tile appears in (home, search, library, playlists, artist pages).
    final isCurrent = player.currentSong?.id == song.id;
    final isActive = isCurrent && player.isPlaying;

    return ListTile(
      dense: dense,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
      onTap: onTap,
      leading: index != null
          ? SizedBox(
              width: 32,
              child: Center(
                child: isCurrent
                    ? Icon(isActive ? Icons.graphic_eq : Icons.pause, color: AppColors.accent, size: 18)
                    : Text('$index', style: const TextStyle(color: AppColors.textSecondary, fontWeight: FontWeight.w600)),
              ),
            )
          : Stack(
              alignment: Alignment.center,
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(6),
                  child: SizedBox(
                    width: 48,
                    height: 48,
                    child: song.thumbnailUrl == null
                        ? Container(
                            color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
                            alignment: Alignment.center,
                            child: Icon(Icons.music_note, color: Colors.white.withValues(alpha: 0.85)),
                          )
                        : CachedNetworkImage(
                            imageUrl: song.thumbnailUrl!,
                            fit: BoxFit.cover,
                            errorWidget: (_, __, ___) => Container(
                              color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
                              alignment: Alignment.center,
                              child: Icon(Icons.music_note, color: Colors.white.withValues(alpha: 0.85)),
                            ),
                          ),
                  ),
                ),
                if (isCurrent)
                  Container(
                    width: 48,
                    height: 48,
                    color: Colors.black.withValues(alpha: 0.45),
                    child: Icon(
                      isActive ? Icons.graphic_eq : Icons.pause,
                      color: AppColors.accent,
                      size: 20,
                    ),
                  ),
              ],
            ),
      title: Text(
        song.title,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          fontWeight: FontWeight.w600,
          color: isCurrent ? AppColors.accent : null,
        ),
      ),
      subtitle: Text(
        song.artistName,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(color: AppColors.textSecondary),
      ),
      trailing: trailing ??
          (index != null
              ? Row(
                  mainAxisSize: MainAxisSize.min,
                  children: const [
                    Icon(Icons.cloud_download_outlined, color: AppColors.textSecondary, size: 20),
                    SizedBox(width: 16),
                    Icon(Icons.drag_handle, color: AppColors.textSecondary, size: 20),
                  ],
                )
              : null),
    );
  }
}
