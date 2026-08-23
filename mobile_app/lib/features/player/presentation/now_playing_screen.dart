import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:just_audio/just_audio.dart';

import '../../../core/theme/app_theme.dart';
import '../../downloads/application/downloads_providers.dart';
import '../../library/application/library_providers.dart';
import '../application/player_providers.dart';

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
      appBar: AppBar(
        leading: IconButton(icon: const Icon(Icons.keyboard_arrow_down), onPressed: () => context.pop()),
        title: const Text('Now Playing'),
      ),
      body: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 32),
        child: Column(
          children: [
            const SizedBox(height: 16),
            Expanded(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(16),
                child: song.thumbnailUrl == null
                    ? Container(
                        color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
                        alignment: Alignment.center,
                        child: Icon(Icons.music_note, size: 64, color: Colors.white.withValues(alpha: 0.85)),
                      )
                    : CachedNetworkImage(
                        imageUrl: song.thumbnailUrl!,
                        fit: BoxFit.cover,
                        width: double.infinity,
                        errorWidget: (_, __, ___) => Container(
                          color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
                          alignment: Alignment.center,
                          child: Icon(Icons.music_note, size: 64, color: Colors.white.withValues(alpha: 0.85)),
                        ),
                      ),
              ),
            ),
            const SizedBox(height: 24),
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(song.title, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold), maxLines: 1, overflow: TextOverflow.ellipsis),
                      Text(song.artistName, style: const TextStyle(color: AppColors.textSecondary, fontSize: 16)),
                    ],
                  ),
                ),
                IconButton(
                  icon: Icon(song.isLiked ? Icons.favorite : Icons.favorite_border, color: song.isLiked ? AppColors.accent : null),
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
            const SizedBox(height: 8),
            Slider(
              value: position.inMilliseconds.toDouble().clamp(0, duration.inMilliseconds.toDouble().clamp(1, double.infinity)),
              max: duration.inMilliseconds.toDouble().clamp(1, double.infinity),
              onChanged: (value) => player.seek(Duration(milliseconds: value.round())),
            ),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(_formatDuration(position), style: const TextStyle(color: AppColors.textSecondary)),
                Text(_formatDuration(duration), style: const TextStyle(color: AppColors.textSecondary)),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                IconButton(
                  icon: Icon(Icons.shuffle, color: player.shuffleEnabled ? AppColors.accent : AppColors.textSecondary),
                  onPressed: player.toggleShuffle,
                ),
                IconButton(icon: const Icon(Icons.skip_previous, size: 36), onPressed: player.previous),
                IconButton.filled(
                  iconSize: 36,
                  icon: Icon(player.isPlaying ? Icons.pause : Icons.play_arrow),
                  onPressed: player.togglePlayPause,
                ),
                IconButton(icon: const Icon(Icons.skip_next, size: 36), onPressed: player.next),
                IconButton(
                  icon: Icon(
                    player.loopMode == LoopMode.one ? Icons.repeat_one : Icons.repeat,
                    color: player.loopMode == LoopMode.off ? AppColors.textSecondary : AppColors.accent,
                  ),
                  onPressed: player.cycleRepeatMode,
                ),
              ],
            ),
            const SizedBox(height: 16),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                IconButton(
                  icon: downloadManager.isActive(song.id)
                      ? SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2, value: downloadManager.progressFor(song.id)),
                        )
                      : const Icon(Icons.download_outlined),
                  onPressed: downloadManager.isActive(song.id) ? null : () => downloadManager.download(song),
                ),
                IconButton(
                  icon: const Icon(Icons.lyrics_outlined),
                  onPressed: () => context.push('/lyrics/${song.id}'),
                ),
              ],
            ),
            const SizedBox(height: 24),
          ],
        ),
      ),
    );
  }
}
