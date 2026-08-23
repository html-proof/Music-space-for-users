import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_theme.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/widgets/media_card.dart';
import '../../../shared/widgets/song_tile.dart';
import '../../player/application/player_providers.dart';
import '../../playlist/application/playlist_providers.dart';
import '../application/library_providers.dart';

class LibraryScreen extends ConsumerStatefulWidget {
  const LibraryScreen({super.key});

  @override
  ConsumerState<LibraryScreen> createState() => _LibraryScreenState();
}

class _LibraryScreenState extends ConsumerState<LibraryScreen> with SingleTickerProviderStateMixin {
  late final TabController _tabController = TabController(length: 4, vsync: this);

  Future<void> _createPlaylist() async {
    final controller = TextEditingController();
    final title = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('New playlist'),
        content: TextField(controller: controller, autofocus: true, decoration: const InputDecoration(hintText: 'Playlist name')),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(context, controller.text.trim()), child: const Text('Create')),
        ],
      ),
    );
    if (title == null || title.isEmpty) return;
    final playlist = await ref.read(playlistRepositoryProvider).create(title: title);
    ref.invalidate(myPlaylistsProvider);
    if (!mounted) return;
    context.push('/playlist/${playlist.id}');
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Your Library'),
        bottom: TabBar(
          controller: _tabController,
          isScrollable: true,
          tabs: const [
            Tab(text: 'Liked'),
            Tab(text: 'Playlists'),
            Tab(text: 'Albums'),
            Tab(text: 'Artists'),
          ],
        ),
        actions: [
          IconButton(icon: const Icon(Icons.add), onPressed: _createPlaylist),
        ],
      ),
      body: TabBarView(
        controller: _tabController,
        children: [
          _LikedSongsTab(),
          _PlaylistsTab(),
          _AlbumsTab(),
          _ArtistsTab(),
        ],
      ),
    );
  }
}

class _LikedSongsTab extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final liked = ref.watch(likedSongsProvider);
    return AsyncValueView(
      value: liked,
      onRetry: () => ref.invalidate(likedSongsProvider),
      data: (songs) {
        if (songs.isEmpty) {
          return const Center(child: Text('No liked songs yet', style: TextStyle(color: AppColors.textSecondary)));
        }
        return ListView.builder(
          itemCount: songs.length,
          itemBuilder: (context, index) => SongTile(
            song: songs[index],
            onTap: () => ref.read(playerControllerProvider).playQueue(songs, startIndex: index),
          ),
        );
      },
    );
  }
}

class _PlaylistsTab extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final playlists = ref.watch(myPlaylistsProvider);
    return AsyncValueView(
      value: playlists,
      onRetry: () => ref.invalidate(myPlaylistsProvider),
      data: (items) {
        if (items.isEmpty) {
          return const Center(child: Text('No playlists yet', style: TextStyle(color: AppColors.textSecondary)));
        }
        return ListView.builder(
          itemCount: items.length,
          itemBuilder: (context, index) {
            final playlist = items[index];
            return ListTile(
              leading: const CircleAvatar(backgroundColor: AppColors.surfaceRaised, child: Icon(Icons.queue_music)),
              title: Text(playlist.title),
              subtitle: Text('${playlist.songCount} songs'),
              onTap: () => context.push('/playlist/${playlist.id}'),
            );
          },
        );
      },
    );
  }
}

class _AlbumsTab extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final albums = ref.watch(savedAlbumsProvider);
    return AsyncValueView(
      value: albums,
      onRetry: () => ref.invalidate(savedAlbumsProvider),
      data: (items) {
        if (items.isEmpty) {
          return const Center(child: Text('No saved albums yet', style: TextStyle(color: AppColors.textSecondary)));
        }
        return GridView.builder(
          padding: const EdgeInsets.all(16),
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 2,
            mainAxisSpacing: 16,
            crossAxisSpacing: 12,
            childAspectRatio: 0.75,
          ),
          itemCount: items.length,
          itemBuilder: (context, index) {
            final album = items[index];
            return MediaCard(
              title: album.title,
              subtitle: album.artistName,
              imageUrl: album.coverUrl,
              width: 160,
              onTap: () => context.push('/album/${album.id}', extra: album),
            );
          },
        );
      },
    );
  }
}

class _ArtistsTab extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final artists = ref.watch(followedArtistsProvider);
    return AsyncValueView(
      value: artists,
      onRetry: () => ref.invalidate(followedArtistsProvider),
      data: (items) {
        if (items.isEmpty) {
          return const Center(child: Text('No followed artists yet', style: TextStyle(color: AppColors.textSecondary)));
        }
        return GridView.builder(
          padding: const EdgeInsets.all(16),
          gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 3,
            mainAxisSpacing: 16,
            crossAxisSpacing: 12,
            childAspectRatio: 0.75,
          ),
          itemCount: items.length,
          itemBuilder: (context, index) {
            final artist = items[index];
            return MediaCard(
              title: artist.name,
              imageUrl: artist.imageUrl,
              circular: true,
              width: 100,
              onTap: () => context.push('/artist/${artist.id}', extra: artist),
            );
          },
        );
      },
    );
  }
}
