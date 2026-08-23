import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_exception.dart';
import '../../../core/theme/app_theme.dart';
import '../../../shared/models/album.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/widgets/song_tile.dart';
import '../../library/application/library_providers.dart';
import '../../player/application/player_providers.dart';
import '../application/album_providers.dart';

class AlbumScreen extends ConsumerStatefulWidget {
  const AlbumScreen({super.key, required this.albumId, this.initialAlbum});

  final String albumId;
  final Album? initialAlbum;

  @override
  ConsumerState<AlbumScreen> createState() => _AlbumScreenState();
}

class _AlbumScreenState extends ConsumerState<AlbumScreen> {
  bool? _isSaved;

  Future<void> _toggleSave() async {
    final repo = ref.read(libraryRepositoryProvider);
    final current = _isSaved ?? widget.initialAlbum?.isSaved ?? false;
    try {
      if (current) {
        await repo.unsaveAlbum(widget.albumId);
      } else {
        await repo.saveAlbum(widget.albumId);
      }
      setState(() => _isSaved = !current);
    } on ApiException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final seokey = widget.initialAlbum?.seokey ?? widget.albumId;
    final details = ref.watch(albumDetailsProvider(seokey));
    final saved = _isSaved ?? widget.initialAlbum?.isSaved ?? false;

    return Scaffold(
      body: AsyncValueView(
        value: details,
        onRetry: () => ref.invalidate(albumDetailsProvider(seokey)),
        data: (result) {
          final album = result ?? widget.initialAlbum;
          final tracks = album?.tracks ?? const [];
          return CustomScrollView(
            slivers: [
              SliverAppBar(
                expandedHeight: 320,
                pinned: true,
                flexibleSpace: FlexibleSpaceBar(
                  background: Padding(
                    padding: const EdgeInsets.only(top: 60),
                    child: Column(
                      children: [
                        ClipRRect(
                          borderRadius: BorderRadius.circular(12),
                          child: SizedBox(
                            width: 180,
                            height: 180,
                            child: album?.coverUrl == null
                                ? Container(color: AppColors.surfaceRaised)
                                : CachedNetworkImage(imageUrl: album!.coverUrl!, fit: BoxFit.cover),
                          ),
                        ),
                        const SizedBox(height: 12),
                        Text(album?.title ?? '', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
                        Text(album?.artistName ?? '', style: const TextStyle(color: AppColors.textSecondary)),
                      ],
                    ),
                  ),
                ),
              ),
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Row(
                    children: [
                      ElevatedButton.icon(
                        onPressed: tracks.isEmpty
                            ? null
                            : () => ref.read(playerControllerProvider).playQueue(tracks),
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('Play all'),
                      ),
                      const SizedBox(width: 12),
                      IconButton(
                        icon: Icon(saved ? Icons.favorite : Icons.favorite_border),
                        color: saved ? AppColors.accent : null,
                        onPressed: _toggleSave,
                      ),
                    ],
                  ),
                ),
              ),
              SliverList(
                delegate: SliverChildBuilderDelegate(
                  (context, index) {
                    final song = tracks[index];
                    return SongTile(
                      song: song,
                      onTap: () => ref.read(playerControllerProvider).playQueue(tracks, startIndex: index),
                    );
                  },
                  childCount: tracks.length,
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
