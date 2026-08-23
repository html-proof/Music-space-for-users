import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/theme/app_theme.dart';
import '../../../shared/widgets/async_value_view.dart';
import '../../../shared/models/download.dart';
import '../application/downloads_providers.dart';

class DownloadsScreen extends ConsumerWidget {
  const DownloadsScreen({super.key});

  String _formatBytes(int bytes) {
    if (bytes <= 0) return '0 MB';
    final mb = bytes / (1024 * 1024);
    if (mb >= 1024) return '${(mb / 1024).toStringAsFixed(2)} GB';
    return '${mb.toStringAsFixed(1)} MB';
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final downloads = ref.watch(downloadsListProvider);
    final storage = ref.watch(downloadsStorageProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Downloads'),
        actions: [
          IconButton(
            icon: const Icon(Icons.delete_sweep_outlined),
            tooltip: 'Remove all downloads',
            onPressed: () async {
              final confirmed = await showDialog<bool>(
                context: context,
                builder: (context) => AlertDialog(
                  title: const Text('Remove all downloads?'),
                  content: const Text('This deletes every downloaded song from this device.'),
                  actions: [
                    TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
                    TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Remove all')),
                  ],
                ),
              );
              if (confirmed == true) {
                await ref.read(downloadManagerProvider).removeAll();
                ref.invalidate(downloadsListProvider);
                ref.invalidate(downloadsStorageProvider);
              }
            },
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(downloadsListProvider);
          ref.invalidate(downloadsStorageProvider);
        },
        child: ListView(
          children: [
            storage.when(
              data: (summary) => Padding(
                padding: const EdgeInsets.all(16),
                child: Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('${summary.completedDownloads} songs downloaded'),
                            const SizedBox(height: 4),
                            Text(_formatBytes(summary.totalBytes),
                                style: const TextStyle(color: AppColors.textSecondary)),
                          ],
                        ),
                        const Icon(Icons.download_done, color: AppColors.accent, size: 32),
                      ],
                    ),
                  ),
                ),
              ),
              loading: () => const SizedBox.shrink(),
              error: (_, __) => const SizedBox.shrink(),
            ),
            AsyncValueView(
              value: downloads,
              onRetry: () => ref.invalidate(downloadsListProvider),
              data: (records) {
                if (records.isEmpty) {
                  return const Padding(
                    padding: EdgeInsets.all(32),
                    child: Center(child: Text('No downloads yet', style: TextStyle(color: AppColors.textSecondary))),
                  );
                }
                return Column(
                  children: records.map((record) => _DownloadTile(record: record)).toList(),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}

class _DownloadTile extends ConsumerWidget {
  const _DownloadTile({required this.record});

  final DownloadRecord record;

  IconData _statusIcon() {
    switch (record.status) {
      case 'completed':
        return Icons.check_circle;
      case 'failed':
        return Icons.error;
      case 'downloading':
      case 'queued':
        return Icons.downloading;
      default:
        return Icons.pause_circle;
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return ListTile(
      leading: Icon(_statusIcon(),
          color: record.isFailed
              ? AppColors.error
              : record.isCompleted
                  ? AppColors.success
                  : AppColors.accent),
      title: Text(record.title, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        record.isFailed
            ? (record.errorMessage ?? 'Failed')
            : record.isActive
                ? '${record.progressPercent}% • ${record.status}'
                : record.artistName,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: IconButton(
        icon: const Icon(Icons.delete_outline),
        onPressed: () async {
          await ref.read(downloadManagerProvider).removeDownload(record);
          ref.invalidate(downloadsListProvider);
          ref.invalidate(downloadsStorageProvider);
        },
      ),
    );
  }
}
