import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme/app_theme.dart';
import '../../../shared/models/artist.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/widgets/media_card.dart';
import '../../search/application/search_providers.dart';
import '../application/onboarding_providers.dart';

/// Artists shown here always come from the backend -- the initial grid is
/// GET /api/onboarding/artists/suggestions (ranked by real catalog data),
/// and typing in the search box calls /api/search rather than filtering a
/// hardcoded list.
class ArtistSelectionScreen extends ConsumerStatefulWidget {
  const ArtistSelectionScreen({super.key});

  @override
  ConsumerState<ArtistSelectionScreen> createState() => _ArtistSelectionScreenState();
}

class _ArtistSelectionScreenState extends ConsumerState<ArtistSelectionScreen> {
  final _controller = TextEditingController();
  final Map<String, Artist> _selected = {};
  List<Artist>? _searchResults;
  bool _searching = false;
  bool _saving = false;

  Future<void> _search(String query) async {
    if (query.trim().length < 2) {
      setState(() => _searchResults = null);
      return;
    }
    setState(() => _searching = true);
    try {
      final results = await ref.read(searchRepositoryProvider).search(query, type: 'artist', limit: 20);
      if (!mounted) return;
      setState(() => _searchResults = results.artists);
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  Future<void> _finish() async {
    setState(() => _saving = true);
    try {
      final repo = ref.read(onboardingRepositoryProvider);
      if (_selected.isNotEmpty) {
        await repo.setArtists(_selected.keys.toList());
      }
      await repo.complete();
      ref.invalidate(onboardingStatusProvider);
      if (!mounted) return;
      context.go('/home');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Widget _grid(List<Artist> artists) {
    if (artists.isEmpty) {
      return const Center(child: Text('No artists found', style: TextStyle(color: AppColors.textSecondary)));
    }
    return GridView.builder(
      padding: const EdgeInsets.all(16),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 3,
        mainAxisSpacing: 16,
        crossAxisSpacing: 12,
        childAspectRatio: 0.75,
      ),
      itemCount: artists.length,
      itemBuilder: (context, index) {
        final artist = artists[index];
        final isSelected = _selected.containsKey(artist.id);
        return Stack(
          children: [
            MediaCard(
              title: artist.name,
              imageUrl: artist.imageUrl,
              circular: true,
              width: 100,
              onTap: () {
                setState(() {
                  if (isSelected) {
                    _selected.remove(artist.id);
                  } else {
                    _selected[artist.id] = artist;
                  }
                });
              },
            ),
            if (isSelected)
              const Positioned(
                right: 8,
                top: 0,
                child: CircleAvatar(
                  radius: 10,
                  backgroundColor: AppColors.accent,
                  child: Icon(Icons.check, size: 14, color: Colors.white),
                ),
              ),
          ],
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final suggested = ref.watch(suggestedArtistsProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('Choose artists you like')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.all(16),
            child: TextField(
              controller: _controller,
              onChanged: _search,
              decoration: InputDecoration(
                hintText: 'Search for more artists...',
                prefixIcon: const Icon(Icons.search),
                filled: true,
                fillColor: AppColors.surfaceRaised,
                border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide.none),
              ),
            ),
          ),
          if (_selected.isNotEmpty)
            SizedBox(
              height: 40,
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 16),
                children: _selected.values
                    .map((a) => Padding(
                          padding: const EdgeInsets.only(right: 8),
                          child: Chip(
                            label: Text(a.name),
                            onDeleted: () => setState(() => _selected.remove(a.id)),
                          ),
                        ))
                    .toList(),
              ),
            ),
          const SizedBox(height: 8),
          Expanded(
            child: _searching
                ? const Center(child: CircularProgressIndicator(color: AppColors.accent))
                : _searchResults != null
                    ? _grid(_searchResults!)
                    : AsyncValueView(
                        value: suggested,
                        onRetry: () => ref.invalidate(suggestedArtistsProvider),
                        data: _grid,
                      ),
          ),
          SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _saving ? null : _finish,
                  child: _saving
                      ? const SizedBox(
                          width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : Text(_selected.isEmpty ? 'Skip for now' : 'Continue'),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
