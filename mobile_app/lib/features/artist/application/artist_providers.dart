import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/providers/core_providers.dart';
import '../data/artist_repository.dart';

final artistRepositoryProvider = Provider<ArtistRepository>((ref) {
  return ArtistRepository(ref.watch(apiClientProvider));
});

final artistDetailsProvider = FutureProvider.autoDispose.family((ref, String seokey) {
  return ref.watch(artistRepositoryProvider).getDetails(seokey);
});
