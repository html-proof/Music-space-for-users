import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:just_audio/just_audio.dart';

import '../../../core/theme/app_theme.dart';
import '../../../shared/widgets/circle_icon_button.dart';
import '../../../shared/widgets/primary_play_button.dart';
import '../../downloads/application/downloads_providers.dart';
import '../../library/application/library_providers.dart';
import '../application/player_providers.dart';

/// Full-bleed photographic layout: the song artwork covers the entire
/// screen behind a dark scrim, with white overlay text/controls on top --
/// matches the reference design's player screen, instead of a rounded
/// artwork card sitting on a plain background.
class NowPlayingScreen extends ConsumerWidget {
  const NowPlayingScreen({super.key});

  String _formatDuration(Duration d) {
    final minutes = d.inMinutes.remainder(60).toString().padLeft(1, '0');
    final seconds = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$minutes:$seconds';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final player = ref.watch(playerControllerProvider);
    final song = player.currentSong;
    final downloadManager = ref.watch(downloadManagerProvider);

    if (song == null) {
      return const Scaffold(body: Center(child: Text('Nothing is playing')));
    }

    final duration = player.duration ?? Duration.zero;
    final position = player.position > duration ? duration : player.position;

    return Scaffold(
      backgroundColor: Colors.black,
      extendBodyBehindAppBar: true,
      body: Stack(
        fit: StackFit.expand,
        children: [
          song.thumbnailUrl == null
              ? Container(color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id))
              : CachedNetworkImage(
                  imageUrl: song.thumbnailUrl!,
                  fit: BoxFit.cover,
                  errorWidget: (_, __, ___) => Container(
                    color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
                  ),
                ),
          // Scrim gradient: near-clear at the top (for the app bar
          // buttons), darkest at the bottom (for text/transport legibility).
          const DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Colors.black45, Colors.transparent, Colors.black87],
                stops: [0.0, 0.35, 1.0],
              ),
            ),
          ),
          SafeArea(
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      CircleIconButton(
                        icon: Icons.keyboard_arrow_down,
                        background: Colors.black.withValues(alpha: 0.35),
                        iconColor: Colors.white,
                        onPressed: () => context.pop(),
                      ),
                      CircleIconButton(
                        icon: Icons.more_vert,
                        background: Colors.black.withValues(alpha: 0.35),
                        iconColor: Colors.white,
                        onPressed: () => context.push('/lyrics/${song.id}'),
                      ),
                    ],
                  ),
                ),
                const Spacer(),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 28),
                  child: Row(
                    children: [
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              song.title,
                              style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.bold),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            const SizedBox(height: 4),
                            Text(
                              song.artistName,
                              style: TextStyle(color: Colors.white.withValues(alpha: 0.75), fontSize: 15),
                            ),
                            // The album is the one piece of context the full
                            // player was missing, and the most natural place to
                            // offer "take me there" -- a song row can only do
                            // it on long-press, which is easy to miss.
                            if ((song.albumId ?? '').isNotEmpty &&
                                song.albumName.trim().isNotEmpty) ...[
                              const SizedBox(height: 6),
                              InkWell(
                                onTap: () => context.push('/album/${song.albumId}'),
                                borderRadius: BorderRadius.circular(4),
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(vertical: 2),
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      Icon(Icons.album_outlined,
                                          size: 14,
                                          color: Colors.white.withValues(alpha: 0.65)),
                                      const SizedBox(width: 6),
                                      Flexible(
                                        child: Text(
                                          song.albumName,
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          style: TextStyle(
                                            color: Colors.white.withValues(alpha: 0.65),
                                            fontSize: 13,
                                            decoration: TextDecoration.underline,
                                          ),
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            ],
                          ],
                        ),
                      ),
                      IconButton(
                        icon: Icon(
                          song.isLiked ? Icons.favorite : Icons.favorite_border,
                          color: song.isLiked ? AppColors.accent : Colors.white,
                        ),
                        onPressed: () async {
                          final repo = ref.read(libraryRepositoryProvider);
                          if (song.isLiked) {
                            await repo.unlikeSong(song.id);
                          } else {
                            await repo.likeSong(song.id);
                          }
                        },
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 4),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: SliderTheme(
                    data: SliderTheme.of(context).copyWith(
                      activeTrackColor: Colors.white,
                      inactiveTrackColor: Colors.white.withValues(alpha: 0.3),
                      thumbColor: Colors.white,
                      trackHeight: 3,
                    ),
                    child: Slider(
                      value: position.inMilliseconds.toDouble().clamp(0, duration.inMilliseconds.toDouble().clamp(1, double.infinity)),
                      max: duration.inMilliseconds.toDouble().clamp(1, double.infinity),
                      onChanged: (value) => player.seek(Duration(milliseconds: value.round())),
                    ),
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 28),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(_formatDuration(position), style: TextStyle(color: Colors.white.withValues(alpha: 0.75))),
                      Text(_formatDuration(duration), style: TextStyle(color: Colors.white.withValues(alpha: 0.75))),
                    ],
                  ),
                ),
                const SizedBox(height: 12),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    IconButton(
                      icon: Icon(Icons.shuffle, color: player.shuffleEnabled ? AppColors.accent : Colors.white70),
                      onPressed: player.toggleShuffle,
                    ),
                    IconButton(
                      icon: const Icon(Icons.skip_previous, size: 36, color: Colors.white),
                      onPressed: player.previous,
                    ),
                    PrimaryPlayButton(
                      size: 64,
                      icon: player.isPlaying ? Icons.pause : Icons.play_arrow,
                      onPressed: player.togglePlayPause,
                    ),
                    IconButton(
                      icon: const Icon(Icons.skip_next, size: 36, color: Colors.white),
                      onPressed: player.next,
                    ),
                    IconButton(
                      icon: Icon(
                        player.loopMode == LoopMode.one ? Icons.repeat_one : Icons.repeat,
                        color: player.loopMode == LoopMode.off ? Colors.white70 : AppColors.accent,
                      ),
                      onPressed: player.cycleRepeatMode,
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    IconButton(
                      icon: downloadManager.isActive(song.id)
                          ? SizedBox(
                              width: 20,
                              height: 20,
                              child: CircularProgressIndicator(
                                strokeWidth: 2,
                                value: downloadManager.progressFor(song.id),
                                color: Colors.white,
                              ),
                            )
                          : const Icon(Icons.download_outlined, color: Colors.white70),
                      onPressed: downloadManager.isActive(song.id) ? null : () => downloadManager.download(song),
                    ),
                    IconButton(
                      icon: const Icon(Icons.lyrics_outlined, color: Colors.white70),
                      onPressed: () => context.push('/lyrics/${song.id}'),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
