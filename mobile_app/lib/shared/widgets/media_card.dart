import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import 'artwork_play_overlay.dart';

/// A single artwork tile used in horizontal rails (home sections, search
/// results grids). `circular` renders artist avatars.
///
/// Missing artwork falls back to a vivid, stable-per-item color block
/// (AppColors.tileColorFor) rather than a flat gray box -- matches the
/// color-blocked playlist/artist tile look the design leans on, and means a
/// thin catalog never renders as a wall of identical gray squares.
class MediaCard extends StatelessWidget {
  const MediaCard({
    super.key,
    required this.title,
    this.subtitle,
    this.imageUrl,
    required this.onTap,
    this.circular = false,
    this.width = 140,
    this.onPlayTap,
  });

  final String title;
  final String? subtitle;
  final String? imageUrl;
  final VoidCallback onTap;
  final bool circular;
  final double width;

  /// When set, shows the small black play-button overlay on the artwork's
  /// bottom-right corner (songs/albums), matching the reference design.
  /// Left null for artist avatars, where tapping the tile itself navigates
  /// rather than plays.
  final VoidCallback? onPlayTap;

  Widget _fallback() {
    return Container(
      color: AppColors.tileColorFor(title),
      alignment: Alignment.center,
      child: Icon(
        circular ? Icons.person : Icons.music_note,
        color: Colors.white.withValues(alpha: 0.85),
        size: 36,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final radius = circular ? BorderRadius.circular(width) : BorderRadius.circular(10);
    return GestureDetector(
      onTap: onTap,
      child: SizedBox(
        width: width,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            ClipRRect(
              borderRadius: radius,
              child: SizedBox(
                width: width,
                height: width,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    imageUrl == null
                        ? _fallback()
                        : CachedNetworkImage(
                            imageUrl: imageUrl!,
                            fit: BoxFit.cover,
                            errorWidget: (_, __, ___) => _fallback(),
                          ),
                    if (onPlayTap != null)
                      Positioned(
                        right: 6,
                        bottom: 6,
                        child: ArtworkPlayOverlay(onTap: onPlayTap!),
                      ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13),
            ),
            if (subtitle != null)
              Text(
                subtitle!,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: AppColors.textSecondary, fontSize: 12),
              ),
          ],
        ),
      ),
    );
  }
}
