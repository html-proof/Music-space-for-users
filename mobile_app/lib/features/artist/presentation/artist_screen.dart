import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/network/api_exception.dart';
import '../../../core/theme/app_theme.dart';
import '../../../shared/models/artist.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/widgets/song_tile.dart';
import '../../library/application/library_providers.dart';
import '../../player/application/player_providers.dart';
import '../application/artist_providers.dart';

class ArtistScreen extends ConsumerStatefulWidget {
  const ArtistScreen({super.key, required this.artistId, this.initialArtist});

  final String artistId;
  final Artist? initialArtist;

  @override
  ConsumerState<ArtistScreen> createState() => _ArtistScreenState();
}

class _ArtistScreenState extends ConsumerState<ArtistScreen> {
  bool? _isFollowed;

  Future<void> _toggleFollow() async {
    final repo = ref.read(libraryRepositoryProvider);
    final current = _isFollowed ?? widget.initialArtist?.isFollowed ?? false;
    try {
      if (current) {
        await repo.unfollowArtist(widget.artistId);
      } else {
        await repo.followArtist(widget.artistId);
      }
      setState(() => _isFollowed = !current);
    } on ApiException catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final seokey = widget.initialArtist?.seokey ?? widget.artistId;
    final details = ref.watch(artistDetailsProvider(seokey));
    final followed = _isFollowed ?? widget.initialArtist?.isFollowed ?? false;

    return Scaffold(
      body: AsyncValueView(
        value: details,
        onRetry: () => ref.invalidate(artistDetailsProvider(seokey)),
        data: (result) {
          final artist = result?.artist ?? widget.initialArtist;
          final tracks = result?.topTracks ?? const [];
          return CustomScrollView(
            slivers: [
              SliverAppBar(
                expandedHeight: 280,
                pinned: true,
                flexibleSpace: FlexibleSpaceBar(
                  title: Text(artist?.name ?? ''),
                  background: artist?.imageUrl == null
                      ? Container(color: AppColors.surfaceRaised)
                      : CachedNetworkImage(imageUrl: artist!.imageUrl!, fit: BoxFit.cover),
                ),
              ),
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Row(
                    children: [
                      if (artist != null)
                        Text('${artist.songCount} songs • ${artist.albumCount} albums',
                            style: const TextStyle(color: AppColors.textSecondary)),
                      const Spacer(),
                      OutlinedButton.icon(
                        onPressed: _toggleFollow,
                        icon: Icon(followed ? Icons.check : Icons.add),
                        label: Text(followed ? 'Following' : 'Follow'),
                      ),
                    ],
                  ),
                ),
              ),
              const SliverToBoxAdapter(
                child: Padding(
                  padding: EdgeInsets.fromLTRB(16, 8, 16, 8),
                  child: Text('Popular', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
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
