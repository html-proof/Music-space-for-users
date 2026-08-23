import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_theme.dart';
import '../../../shared/models/lyrics.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../player/application/player_providers.dart';
import '../application/lyrics_providers.dart';

class LyricsScreen extends ConsumerWidget {
  const LyricsScreen({super.key, required this.songId});

  final String songId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final lyrics = ref.watch(lyricsProvider(songId));
    final song = ref.watch(playerControllerProvider).currentSong;

    return Scaffold(
      appBar: AppBar(title: Text(song?.title ?? 'Lyrics')),
      body: AsyncValueView(
        value: lyrics,
        onRetry: () => ref.invalidate(lyricsProvider(songId)),
        data: (data) {
          if (!data.hasLyrics) {
            return const Center(
              child: Padding(
                padding: EdgeInsets.all(32),
                child: Text('No lyrics available for this song.', style: TextStyle(color: AppColors.textSecondary)),
              ),
            );
          }
          return data.isSynced ? _SyncedLyricsView(data: data) : _PlainLyricsView(data: data);
        },
      ),
    );
  }
}

class _PlainLyricsView extends StatelessWidget {
  const _PlainLyricsView({required this.data});

  final LyricsData data;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: Text(
        data.plainText ?? '',
        style: const TextStyle(fontSize: 18, height: 1.8, color: AppColors.textPrimary),
      ),
    );
  }
}

class _SyncedLyricsView extends ConsumerStatefulWidget {
  const _SyncedLyricsView({required this.data});

  final LyricsData data;

  @override
  ConsumerState<_SyncedLyricsView> createState() => _SyncedLyricsViewState();
}

class _SyncedLyricsViewState extends ConsumerState<_SyncedLyricsView> {
  final _scrollController = ScrollController();
  int _activeIndex = -1;

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  int _indexForPosition(Duration position) {
    final lines = widget.data.syncedLines;
    int index = -1;
    for (var i = 0; i < lines.length; i++) {
      if (position.inMilliseconds >= lines[i].timeMs) {
        index = i;
      } else {
        break;
      }
    }
    return index;
  }

  @override
  Widget build(BuildContext context) {
    final player = ref.watch(playerControllerProvider);
    final activeIndex = _indexForPosition(player.position);
    if (activeIndex != _activeIndex) {
      _activeIndex = activeIndex;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!_scrollController.hasClients || activeIndex < 0) return;
        _scrollController.animateTo(
          (activeIndex * 48).toDouble().clamp(0, _scrollController.position.maxScrollExtent),
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      });
    }

    return ListView.builder(
      controller: _scrollController,
      padding: const EdgeInsets.symmetric(vertical: 120, horizontal: 24),
      itemCount: widget.data.syncedLines.length,
      itemBuilder: (context, index) {
        final line = widget.data.syncedLines[index];
        final isActive = index == activeIndex;
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Text(
            line.text,
            style: TextStyle(
              fontSize: isActive ? 22 : 17,
              fontWeight: isActive ? FontWeight.bold : FontWeight.normal,
              color: isActive ? AppColors.accent : AppColors.textSecondary,
            ),
          ),
        );
      },
    );
  }
}
