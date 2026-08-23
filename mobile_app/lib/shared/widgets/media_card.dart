import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';

/// A single artwork tile used in horizontal rails (home sections, search
/// results grids). `circular` renders artist avatars.
class MediaCard extends StatelessWidget {
  const MediaCard({
    super.key,
    required this.title,
    this.subtitle,
    this.imageUrl,
    required this.onTap,
    this.circular = false,
    this.width = 140,
  });

  final String title;
  final String? subtitle;
  final String? imageUrl;
  final VoidCallback onTap;
  final bool circular;
  final double width;

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
                child: imageUrl == null
                    ? Container(
                        color: AppColors.surfaceRaised,
                        child: Icon(
                          circular ? Icons.person : Icons.music_note,
                          color: AppColors.textSecondary,
                          size: 36,
                        ),
                      )
                    : CachedNetworkImage(
                        imageUrl: imageUrl!,
                        fit: BoxFit.cover,
                        errorWidget: (_, __, ___) => Container(
                          color: AppColors.surfaceRaised,
                          child: Icon(
                            circular ? Icons.person : Icons.music_note,
                            color: AppColors.textSecondary,
                          ),
                        ),
                      ),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
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
