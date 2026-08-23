import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/downloads_repository.dart';
import 'download_manager.dart';

final downloadsRepositoryProvider = Provider<DownloadsRepository>((ref) {
  return DownloadsRepository(ref.watch(apiClientProvider));
});

final downloadManagerProvider = ChangeNotifierProvider<DownloadManager>((ref) {
  return DownloadManager(
    ref.watch(downloadsRepositoryProvider),
    ref.watch(apiClientProvider),
    ref.watch(localDatabaseProvider),
  );
});

final downloadsListProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(downloadsRepositoryProvider).list();
});

final downloadsStorageProvider = FutureProvider.autoDispose((ref) {
  return ref.watch(downloadsRepositoryProvider).storageSummary();
});
