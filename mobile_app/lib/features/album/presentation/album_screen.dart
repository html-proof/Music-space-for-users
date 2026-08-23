import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_exception.dart';
import '../../../core/theme/app_theme.dart';
import '../../../shared/models/album.dart';
import '../../../shared/widgets/artwork_play_overlay.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/widgets/circle_icon_button.dart';
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
                        SizedBox(
                          width: 180,
                          height: 190,
                          child: Stack(
                            clipBehavior: Clip.none,
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
                              // Overlapping the corner, not inset inside it --
                              // matches the reference's play button sitting
                              // half on/half off the artwork edge.
                              Positioned(
                                right: -8,
                                bottom: -8,
                                child: ArtworkPlayOverlay(
                                  size: 44,
                                  onTap: tracks.isEmpty
                                      ? () {}
                                      : () => ref.read(playerControllerProvider).playQueue(tracks),
                                ),
                              ),
                            ],
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
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      // Light-blue circular shuffle button, matching the
                      // reference's single small accent button on this row --
                      // the play action itself lives on the artwork overlay
                      // above, not a second button here.
                      CircleIconButton(
                        icon: Icons.shuffle,
                        background: AppColors.accent.withValues(alpha: 0.15),
                        iconColor: AppColors.accent,
                        onPressed: tracks.isEmpty
                            ? null
                            : () async {
                                final player = ref.read(playerControllerProvider);
                                if (!player.shuffleEnabled) await player.toggleShuffle();
                                await player.playQueue(tracks);
                              },
                      ),
                      const SizedBox(width: 10),
                      CircleIconButton(
                        icon: saved ? Icons.favorite : Icons.favorite_border,
                        background: AppColors.surfaceRaised,
                        iconColor: saved ? AppColors.accent : AppColors.textPrimary,
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
                      index: index + 1,
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
