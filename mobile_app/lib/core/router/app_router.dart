import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../features/album/presentation/album_screen.dart';
import '../../features/artist/presentation/artist_screen.dart';
import '../../features/auth/presentation/login_screen.dart';
import '../../features/auth/presentation/splash_screen.dart';
import '../../features/downloads/presentation/downloads_screen.dart';
import '../../features/home/presentation/home_screen.dart';
import '../../features/library/presentation/library_screen.dart';
import '../../features/lyrics/presentation/lyrics_screen.dart';
import '../../features/onboarding/presentation/artist_selection_screen.dart';
import '../../features/onboarding/presentation/language_selection_screen.dart';
import '../../features/player/presentation/now_playing_screen.dart';
import '../../features/playlist/presentation/playlist_screen.dart';
import '../../features/profile/presentation/profile_screen.dart';
import '../../features/search/presentation/search_screen.dart';
import '../../features/settings/presentation/settings_screen.dart';
import '../../shared/models/album.dart';
import '../../shared/models/artist.dart';
import '../../shared/widgets/app_shell.dart';

final rootNavigatorKey = GlobalKey<NavigatorState>();
final _shellNavigatorKey = GlobalKey<NavigatorState>();

final GoRouter appRouter = GoRouter(
  navigatorKey: rootNavigatorKey,
  initialLocation: '/splash',
  routes: [
    GoRoute(path: '/splash', builder: (context, state) => const SplashScreen()),
    GoRoute(path: '/login', builder: (context, state) => const LoginScreen()),
    GoRoute(
      path: '/onboarding/languages',
      builder: (context, state) => const LanguageSelectionScreen(),
    ),
    GoRoute(
      path: '/onboarding/artists',
      builder: (context, state) => const ArtistSelectionScreen(),
    ),
    StatefulShellRoute.indexedStack(
      builder: (context, state, navigationShell) => AppShell(navigationShell: navigationShell),
      branches: [
        StatefulShellBranch(
          navigatorKey: _shellNavigatorKey,
          routes: [GoRoute(path: '/home', builder: (context, state) => const HomeScreen())],
        ),
        StatefulShellBranch(
          routes: [GoRoute(path: '/search', builder: (context, state) => const SearchScreen())],
        ),
        StatefulShellBranch(
          routes: [GoRoute(path: '/library', builder: (context, state) => const LibraryScreen())],
        ),
        StatefulShellBranch(
          routes: [GoRoute(path: '/profile', builder: (context, state) => const ProfileScreen())],
        ),
      ],
    ),
    GoRoute(
      path: '/player',
      parentNavigatorKey: rootNavigatorKey,
      pageBuilder: (context, state) => const MaterialPage(
        fullscreenDialog: true,
        child: NowPlayingScreen(),
      ),
    ),
    GoRoute(
      path: '/artist/:id',
      parentNavigatorKey: rootNavigatorKey,
      builder: (context, state) => ArtistScreen(
        artistId: state.pathParameters['id']!,
        initialArtist: state.extra is Artist ? state.extra as Artist : null,
      ),
    ),
    GoRoute(
      path: '/album/:id',
      parentNavigatorKey: rootNavigatorKey,
      builder: (context, state) => AlbumScreen(
        albumId: state.pathParameters['id']!,
        initialAlbum: state.extra is Album ? state.extra as Album : null,
      ),
    ),
    GoRoute(
      path: '/playlist/:id',
      parentNavigatorKey: rootNavigatorKey,
      builder: (context, state) => PlaylistScreen(playlistId: state.pathParameters['id']!),
    ),
    GoRoute(
      path: '/lyrics/:songId',
      parentNavigatorKey: rootNavigatorKey,
      builder: (context, state) => LyricsScreen(songId: state.pathParameters['songId']!),
    ),
    GoRoute(
      path: '/downloads',
      parentNavigatorKey: rootNavigatorKey,
      builder: (context, state) => const DownloadsScreen(),
    ),
    GoRoute(
      path: '/settings',
      parentNavigatorKey: rootNavigatorKey,
      builder: (context, state) => const SettingsScreen(),
    ),
  ],
);
