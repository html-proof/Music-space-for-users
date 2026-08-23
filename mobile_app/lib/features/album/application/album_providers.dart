import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/album_repository.dart';

final albumRepositoryProvider = Provider<AlbumRepository>((ref) {
  return AlbumRepository(ref.watch(apiClientProvider));
});

final albumDetailsProvider = FutureProvider.autoDispose.family((ref, String seokey) {
  return ref.watch(albumRepositoryProvider).getDetails(seokey);
});
