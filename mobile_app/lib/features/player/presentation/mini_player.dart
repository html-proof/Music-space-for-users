import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_theme.dart';
import '../application/player_providers.dart';

class MiniPlayer extends ConsumerWidget {
  const MiniPlayer({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final player = ref.watch(playerControllerProvider);
    final song = player.currentSong;
    if (song == null) return const SizedBox.shrink();

    final duration = player.duration ?? Duration.zero;
    final progress = duration.inMilliseconds == 0
        ? 0.0
        : player.position.inMilliseconds / duration.inMilliseconds;

    return GestureDetector(
      onTap: () => context.push('/player'),
      child: Container(
        height: 64,
        margin: const EdgeInsets.symmetric(horizontal: 8),
        decoration: BoxDecoration(
          color: AppColors.surfaceRaised,
          borderRadius: BorderRadius.circular(12),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            LinearProgressIndicator(
              value: progress.clamp(0.0, 1.0),
              minHeight: 2,
              backgroundColor: AppColors.surface,
              valueColor: const AlwaysStoppedAnimation(AppColors.accent),
            ),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 10),
                child: Row(
                  children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(6),
                      child: SizedBox(
                        width: 40,
                        height: 40,
                        child: song.thumbnailUrl == null
                            ? Container(
                                color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
                                alignment: Alignment.center,
                                child: Icon(Icons.music_note, size: 18, color: Colors.white.withValues(alpha: 0.85)),
                              )
                            : CachedNetworkImage(
                                imageUrl: song.thumbnailUrl!,
                                fit: BoxFit.cover,
                                errorWidget: (_, __, ___) => Container(
                                  color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
                                  alignment: Alignment.center,
                                  child: Icon(Icons.music_note, size: 18, color: Colors.white.withValues(alpha: 0.85)),
                                ),
                              ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(song.title, maxLines: 1, overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
                          Text(song.artistName, maxLines: 1, overflow: TextOverflow.ellipsis,
                              style: const TextStyle(color: AppColors.textSecondary, fontSize: 11)),
                        ],
                      ),
                    ),
                    Container(
                      width: 34,
                      height: 34,
                      margin: const EdgeInsets.symmetric(horizontal: 2),
                      decoration: const BoxDecoration(color: Colors.white, shape: BoxShape.circle),
                      child: IconButton(
                        padding: EdgeInsets.zero,
                        iconSize: 18,
                        color: Colors.black,
                        icon: Icon(player.isPlaying ? Icons.pause : Icons.play_arrow),
                        onPressed: player.togglePlayPause,
                      ),
                    ),
                    IconButton(
                      icon: const Icon(Icons.skip_next),
                      onPressed: player.next,
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
