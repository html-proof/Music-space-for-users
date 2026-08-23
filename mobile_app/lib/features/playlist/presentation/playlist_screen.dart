import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_theme.dart';
import '../../../shared/models/song.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/widgets/song_tile.dart';
import '../../player/application/player_providers.dart';
import '../application/playlist_providers.dart';

class PlaylistScreen extends ConsumerWidget {
  const PlaylistScreen({super.key, required this.playlistId});

  final String playlistId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final playlist = ref.watch(playlistDetailProvider(playlistId));

    return Scaffold(
      appBar: AppBar(
        actions: [
          IconButton(
            icon: const Icon(Icons.delete_outline),
            onPressed: () async {
              final confirmed = await showDialog<bool>(
                context: context,
                builder: (context) => AlertDialog(
                  title: const Text('Delete playlist?'),
                  actions: [
                    TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
                    TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Delete')),
                  ],
                ),
              );
              if (confirmed == true) {
                await ref.read(playlistRepositoryProvider).delete(playlistId);
                ref.invalidate(myPlaylistsProvider);
                if (context.mounted) context.pop();
              }
            },
          ),
        ],
      ),
      body: AsyncValueView(
        value: playlist,
        onRetry: () => ref.invalidate(playlistDetailProvider(playlistId)),
        data: (data) {
          final songs = data.songs.map((e) => e.song).whereType<Song>().toList();
          return CustomScrollView(
            slivers: [
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(data.title, style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
                      if (data.description != null && data.description!.isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text(data.description!, style: const TextStyle(color: AppColors.textSecondary)),
                        ),
                      const SizedBox(height: 4),
                      Text('${data.songCount} songs', style: const TextStyle(color: AppColors.textSecondary)),
                      const SizedBox(height: 12),
                      ElevatedButton.icon(
                        onPressed: songs.isEmpty
                            ? null
                            : () => ref.read(playerControllerProvider).playQueue(songs),
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('Play'),
                      ),
                    ],
                  ),
                ),
              ),
              if (songs.isEmpty)
                const SliverToBoxAdapter(
                  child: Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: Text('No songs in this playlist yet', style: TextStyle(color: AppColors.textSecondary))),
                  ),
                )
              else
                SliverList(
                  delegate: SliverChildBuilderDelegate(
                    (context, index) {
                      final song = songs[index];
                      return SongTile(
                        song: song,
                        onTap: () => ref.read(playerControllerProvider).playQueue(songs, startIndex: index),
                        trailing: IconButton(
                          icon: const Icon(Icons.remove_circle_outline),
                          onPressed: () async {
                            await ref.read(playlistRepositoryProvider).removeSong(playlistId, song.id);
                            ref.invalidate(playlistDetailProvider(playlistId));
                          },
                        ),
                      );
                    },
                    childCount: songs.length,
                  ),
                ),
              const SliverToBoxAdapter(child: SizedBox(height: 32)),
            ],
          );
        },
      ),
    );
  }
}
