import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

import '../../core/theme/app_theme.dart';
import '../models/song.dart';

class SongTile extends StatelessWidget {
  const SongTile({
    super.key,
    required this.song,
    required this.onTap,
    this.trailing,
    this.dense = false,
  });

  final Song song;
  final VoidCallback onTap;
  final Widget? trailing;
  final bool dense;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      dense: dense,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 2),
      onTap: onTap,
      leading: ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: SizedBox(
          width: 48,
          height: 48,
          child: song.thumbnailUrl == null
              ? Container(
                  color: AppColors.surfaceRaised,
                  child: const Icon(Icons.music_note, color: AppColors.textSecondary),
                )
              : CachedNetworkImage(
                  imageUrl: song.thumbnailUrl!,
                  fit: BoxFit.cover,
                  errorWidget: (_, __, ___) => Container(
                    color: AppColors.surfaceRaised,
                    child: const Icon(Icons.music_note, color: AppColors.textSecondary),
                  ),
                ),
        ),
      ),
      title: Text(
        song.title,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(fontWeight: FontWeight.w600),
      ),
      subtitle: Text(
        song.artistName,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(color: AppColors.textSecondary),
      ),
      trailing: trailing,
    );
  }
}
