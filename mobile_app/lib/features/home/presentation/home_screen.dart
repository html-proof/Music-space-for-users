import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_theme.dart';
import '../../../shared/models/song.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/widgets/media_card.dart';
import '../../../shared/widgets/section_header.dart';
import '../../auth/application/auth_providers.dart';
import '../../player/application/player_providers.dart';
import '../application/home_providers.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final feed = ref.watch(homeFeedProvider);
    return Scaffold(
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: () => ref.read(homeFeedProvider.notifier).refresh(),
          child: AsyncValueView(
            value: feed,
            onRetry: () => ref.invalidate(homeFeedProvider),
            data: (data) {
              // Every shelf is fetched live from Gaana, so "no shelves" is a
              // real outcome (Gaana unreachable, or nothing for this user's
              // languages) rather than an impossible one -- the server no
              // longer pads the feed out of the local database. Show the same
              // retry affordance the error branch does instead of a page that
              // is blank under the greeting.
              final hasContent = data.topMix.isNotEmpty ||
                  data.categories.any((category) => category.items.isNotEmpty);
              if (!hasContent) {
                return _EmptyFeed(
                  greeting: data.greeting,
                  onRetry: () => ref.read(homeFeedProvider.notifier).refresh(),
                );
              }

              // The hero spotlights whatever the feed considers the strongest
              // pick for this user: the top of the personalized mix, or the
              // first item of the first shelf if there is no mix yet.
              final heroQueue = data.topMix.isNotEmpty
                  ? data.topMix
                  : (data.categories.isNotEmpty ? data.categories.first.items : const <Song>[]);

              return ListView(
                children: [
                  _HomeHeader(greeting: data.greeting),
                  if (heroQueue.isNotEmpty) _FeaturedHero(song: heroQueue.first, queue: heroQueue),
                  if (data.topMix.isNotEmpty) _SongRail(title: 'Made for you', songs: data.topMix),
                  for (final category in data.categories)
                    if (category.items.isNotEmpty) _SongRail(title: category.title, songs: category.items),
                  const SizedBox(height: 24),
                ],
              );
            },
          ),
        ),
      ),
    );
  }
}

/// Shown when the feed came back with nothing in it.
///
/// Scrollable on purpose: it is the child of a RefreshIndicator, which only
/// arms itself over a scrollable, so pull-to-refresh keeps working here -- the
/// one gesture most likely to fix the situation.
class _EmptyFeed extends StatelessWidget {
  const _EmptyFeed({required this.greeting, required this.onRetry});

  final String greeting;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      children: [
        _HomeHeader(greeting: greeting),
        const SizedBox(height: 64),
        const Icon(Icons.cloud_off, color: AppColors.textSecondary, size: 40),
        const SizedBox(height: 12),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 32),
          child: Text(
            'We could not load recommendations right now.\nPull down or tap retry to try again.',
            textAlign: TextAlign.center,
            style: TextStyle(color: AppColors.textSecondary),
          ),
        ),
        const SizedBox(height: 16),
        Center(child: OutlinedButton(onPressed: onRetry, child: const Text('Retry'))),
      ],
    );
  }
}

/// Avatar + time-of-day greeting header, plus quick actions -- matches the
/// reference design's home header instead of a plain AppBar title.
class _HomeHeader extends ConsumerWidget {
  const _HomeHeader({required this.greeting});

  final String greeting;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final user = ref.watch(currentUserProvider).valueOrNull;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: Row(
        children: [
          CircleAvatar(
            radius: 20,
            backgroundColor: AppColors.surfaceRaised,
            backgroundImage: user?.photoUrl != null ? NetworkImage(user!.photoUrl!) : null,
            child: user?.photoUrl == null
                ? const Icon(Icons.person, color: AppColors.textSecondary)
                : null,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(greeting, style: Theme.of(context).textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.bold)),
                if (user?.displayName != null)
                  Text(user!.displayName!, style: const TextStyle(color: AppColors.textSecondary, fontSize: 13)),
              ],
            ),
          ),
          IconButton(icon: const Icon(Icons.download_outlined), onPressed: () => context.push('/downloads')),
          IconButton(icon: const Icon(Icons.settings_outlined), onPressed: () => context.push('/settings')),
        ],
      ),
    );
  }
}

/// A large "Featured" spotlight card at the top of the feed -- a color-block
/// background with the song's artwork bleeding in from the right edge and
/// title/artist overlaid, matching the reference design's hero treatment
/// (as opposed to just another item in a horizontal rail).
class _FeaturedHero extends ConsumerWidget {
  const _FeaturedHero({required this.song, required this.queue});

  final Song song;
  final List<Song> queue;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
      child: GestureDetector(
        onTap: () => ref.read(playerControllerProvider).playQueue(queue, startIndex: 0),
        child: Container(
          height: 160,
          clipBehavior: Clip.antiAlias,
          decoration: BoxDecoration(
            color: AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
            borderRadius: BorderRadius.circular(20),
          ),
          child: Stack(
            children: [
              Positioned(
                right: 0,
                top: 0,
                bottom: 0,
                width: 150,
                child: song.thumbnailUrl == null
                    ? Container(color: Colors.black.withValues(alpha: 0.15))
                    : CachedNetworkImage(
                        imageUrl: song.thumbnailUrl!,
                        fit: BoxFit.cover,
                        errorWidget: (_, __, ___) => Container(color: Colors.black.withValues(alpha: 0.15)),
                      ),
              ),
              // Fades the artwork into the color block instead of a hard
              // edge, so the overlaid text stays legible either way.
              Positioned.fill(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.centerLeft,
                      end: Alignment.centerRight,
                      colors: [
                        AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id),
                        AppColors.tileColorFor(song.id.isEmpty ? song.title : song.id).withValues(alpha: 0.0),
                      ],
                      stops: const [0.35, 0.85],
                    ),
                  ),
                ),
              ),
              Positioned(
                left: 20,
                right: 90,
                bottom: 18,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text(
                      'FEATURED',
                      style: TextStyle(color: Colors.white70, fontSize: 11, fontWeight: FontWeight.w700, letterSpacing: 1.2),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      song.title,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white, fontSize: 21, fontWeight: FontWeight.w800, height: 1.1),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      song.artistName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: Colors.white70, fontSize: 13),
                    ),
                  ],
                ),
              ),
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
                onPlayTap: () => ref.read(playerControllerProvider).playQueue(songs, startIndex: index),
              );
            },
          ),
        ),
      ],
    );
  }
}
