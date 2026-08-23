import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../shared/models/song.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/widgets/media_card.dart';
import '../../../shared/widgets/section_header.dart';
import '../../player/application/player_providers.dart';
import '../application/home_providers.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final feed = ref.watch(homeFeedProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('GaanaPy'),
        actions: [
          IconButton(icon: const Icon(Icons.download_outlined), onPressed: () => context.push('/downloads')),
          IconButton(icon: const Icon(Icons.settings_outlined), onPressed: () => context.push('/settings')),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async => ref.invalidate(homeFeedProvider),
        child: AsyncValueView(
          value: feed,
          onRetry: () => ref.invalidate(homeFeedProvider),
          data: (data) => ListView(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 0),
                child: Text(data.greeting, style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.bold)),
              ),
              if (data.topMix.isNotEmpty) _SongRail(title: 'Made for you', songs: data.topMix),
              for (final category in data.categories)
                if (category.items.isNotEmpty) _SongRail(title: category.title, songs: category.items),
              const SizedBox(height: 24),
            ],
          ),
        ),
      ),
    );
  }
}

class _SongRail extends ConsumerWidget {
  const _SongRail({required this.title, required this.songs});

  final String title;
  final List<Song> songs;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionHeader(title: title),
        SizedBox(
          height: 190,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 16),
            itemCount: songs.length,
            separatorBuilder: (_, __) => const SizedBox(width: 12),
            itemBuilder: (context, index) {
              final song = songs[index];
              return MediaCard(
                title: song.title,
                subtitle: song.artistName,
                imageUrl: song.thumbnailUrl,
                onTap: () => ref.read(playerControllerProvider).playQueue(songs, startIndex: index),
              );
            },
          ),
        ),
      ],
    );
  }
}
