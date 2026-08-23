import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/storage/local_database.dart';
import '../../../core/theme/app_theme.dart';
import '../../../shared/widgets/media_card.dart';
import '../../../shared/widgets/song_tile.dart';
import '../../player/application/player_providers.dart';
import '../application/search_providers.dart';
import '../data/search_repository.dart';

class SearchScreen extends ConsumerStatefulWidget {
  const SearchScreen({super.key});

  @override
  ConsumerState<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends ConsumerState<SearchScreen> {
  final _controller = TextEditingController();
  Timer? _debounce;
  SearchResults? _results;
  List<String> _recent = [];
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _loadRecent();
  }

  Future<void> _loadRecent() async {
    final recent = await LocalDatabase.instance.recentSearches();
    if (mounted) setState(() => _recent = recent);
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    if (value.trim().isEmpty) {
      setState(() => _results = null);
      return;
    }
    // Matches the "debounce 250-350ms" guidance from the product spec.
    _debounce = Timer(const Duration(milliseconds: 300), () => _runSearch(value));
  }

  Future<void> _runSearch(String query) async {
    setState(() => _loading = true);
    try {
      final results = await ref.read(searchRepositoryProvider).search(query);
      await LocalDatabase.instance.addRecentSearch(query);
      await _loadRecent();
      if (!mounted) return;
      setState(() => _results = results);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: _controller,
          autofocus: false,
          onChanged: _onChanged,
          onSubmitted: _runSearch,
          decoration: InputDecoration(
            hintText: 'Songs, artists, albums, lyrics...',
            filled: true,
            fillColor: AppColors.surfaceRaised,
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: BorderSide.none),
            contentPadding: const EdgeInsets.symmetric(horizontal: 16),
          ),
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: AppColors.accent))
          : _results == null
              ? _RecentSearches(
                  queries: _recent,
                  onTap: (q) {
                    _controller.text = q;
                    _runSearch(q);
                  },
                  onClear: () async {
                    await LocalDatabase.instance.clearRecentSearches();
                    await ref.read(searchRepositoryProvider).clearHistory();
                    await _loadRecent();
                  },
                )
              : _SearchResultsView(results: _results!),
    );
  }
}

class _RecentSearches extends StatelessWidget {
  const _RecentSearches({required this.queries, required this.onTap, required this.onClear});

  final List<String> queries;
  final ValueChanged<String> onTap;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    if (queries.isEmpty) {
      return const Center(
        child: Text('Search for songs, artists, and albums', style: TextStyle(color: AppColors.textSecondary)),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text('Recent searches', style: TextStyle(fontWeight: FontWeight.bold)),
              TextButton(onPressed: onClear, child: const Text('Clear')),
            ],
          ),
        ),
        Expanded(
          child: ListView.builder(
            itemCount: queries.length,
            itemBuilder: (context, index) => ListTile(
              leading: const Icon(Icons.history, color: AppColors.textSecondary),
              title: Text(queries[index]),
              onTap: () => onTap(queries[index]),
            ),
          ),
        ),
      ],
    );
  }
}

class _SearchResultsView extends ConsumerWidget {
  const _SearchResultsView({required this.results});

  final SearchResults results;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (results.isEmpty) {
      return const Center(child: Text('No results found', style: TextStyle(color: AppColors.textSecondary)));
    }
    return ListView(
      children: [
        if (results.artists.isNotEmpty) ...[
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Text('Artists', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
          ),
          SizedBox(
            height: 150,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              itemCount: results.artists.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (context, index) {
                final artist = results.artists[index];
                return MediaCard(
                  title: artist.name,
                  imageUrl: artist.imageUrl,
                  circular: true,
                  width: 100,
                  onTap: () => context.push('/artist/${artist.id}', extra: artist),
                );
              },
            ),
          ),
        ],
        if (results.albums.isNotEmpty) ...[
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Text('Albums', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
          ),
          SizedBox(
            height: 190,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              itemCount: results.albums.length,
              separatorBuilder: (_, __) => const SizedBox(width: 12),
              itemBuilder: (context, index) {
                final album = results.albums[index];
                return MediaCard(
                  title: album.title,
                  subtitle: album.artistName,
                  imageUrl: album.coverUrl,
                  onTap: () => context.push('/album/${album.id}', extra: album),
                );
              },
            ),
          ),
        ],
        if (results.songs.isNotEmpty) ...[
          const Padding(
            padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Text('Songs', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
          ),
          ...results.songs.asMap().entries.map(
                (entry) => SongTile(
                  song: entry.value,
                  onTap: () => ref
                      .read(playerControllerProvider)
                      .playQueue(results.songs, startIndex: entry.key),
                ),
              ),
        ],
        const SizedBox(height: 24),
      ],
    );
  }
}
