"""Candidate generation -- the retrieval stage.

**Every music candidate here comes from Gaana.** Retrieval used to be five
`select(Song)` scans, which made Postgres the de-facto catalog: the home feed
could only ever recommend songs that some earlier request happened to have
ingested, so a fresh deployment recommended nothing and a stale one recommended
last month's ingest forever. The database is now user data only -- what the user
played, liked and playlisted -- and the songs themselves are fetched live.

Three of the five sources are direct Gaana reads, driven by the preferences and
affinities held in Postgres:

    preferences/affinities (Postgres)  ->  Gaana fetch  ->  candidates

    artist_genre  top artists and genres      -> Gaana artist/genre search
    content       strongest genre + language  -> Gaana search, cosine-reranked
    popular       preferred languages         -> Gaana trending + new releases

The remaining two -- `cf` and `playlist` -- are *behavioural*: they score songs
by co-occurrence in real listening sessions and playlists, and the ids they
produce are ids of Gaana tracks users actually played. Reading those rows back
by primary key resolves a user-data reference; it does not browse a catalog, and
neither source can invent a song nobody has ever played.

Provenance is a ranking feature (`src_*`), so the source names are part of the
model contract and are unchanged.

Upstream calls are bounded twice over: `SOURCE_CAPS` limits how many candidates
each source contributes, and `FETCH_BUDGET_SECONDS` limits how long the whole
retrieval stage may spend on the network. `catalog_service` caches each distinct
Gaana call for 30 minutes, so in steady state most of these are cache hits and
the budget is never approached.
"""
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import is_uuid
from app.models.song import Song
from app.models.playlist import PlaylistSong
from app.ml import config, linalg
from app.ml.features import UserState, song_vector

logger = logging.getLogger("ml.candidates")

# Wall-clock ceiling on the upstream fetching done by one `generate` call. Once
# it is spent, the remaining sources contribute nothing rather than pushing the
# request past the client's timeout; whatever was already fetched still ranks,
# and the next request picks up the rest from catalog_service's 30-minute cache.
FETCH_BUDGET_SECONDS = 18.0

# How many distinct upstream entities each Gaana-backed source may fan out over.
# Each one is a separate network call, so these are cost knobs, not quality ones.
MAX_ARTIST_FETCHES = 3
MAX_GENRE_FETCHES = 2
MAX_LANGUAGE_FETCHES = 2
# Per-entity fetch size. Over-fetching here is cheap (one call either way) and
# gives the ranker something to actually choose between.
PER_FETCH_LIMIT = 20


class _Budget:
    """A shared deadline for the upstream calls in one retrieval pass."""

    def __init__(self, seconds: float = FETCH_BUDGET_SECONDS):
        self.deadline = time.monotonic() + seconds

    def spent(self) -> bool:
        return time.monotonic() >= self.deadline


@dataclass
class CandidateSet:
    """Deduplicated candidates plus the sources that produced each one."""
    songs: Dict[str, Any] = field(default_factory=dict)
    sources: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    vectors: Dict[str, Any] = field(default_factory=dict)
    cf_scores: Dict[str, float] = field(default_factory=dict)

    def add(self, song: Any, source: str, vector: Any = None) -> None:
        song_id = str(getattr(song, "id", "") or "")
        if not song_id:
            return
        self.songs.setdefault(song_id, song)
        self.sources[song_id].add(source)
        if vector is not None:
            self.vectors.setdefault(song_id, vector)

    def __len__(self) -> int:
        return len(self.songs)

    def song_list(self) -> List[Any]:
        return list(self.songs.values())

    def source_map(self) -> Dict[str, Set[str]]:
        return {k: set(v) for k, v in self.sources.items()}


def _top(scores: Dict[str, float], n: int) -> List[str]:
    return [k for k, _ in sorted(scores.items(), key=lambda p: p[1], reverse=True)[:n]]


def _preferred(affinity: Dict[str, float], declared: Set[str], n: int) -> List[str]:
    """Observed affinities first, falling back to what the user declared at
    onboarding -- which is all a brand-new user has."""
    ordered = _top(affinity, n)
    for value in declared:
        if len(ordered) >= n:
            break
        if value and value not in ordered:
            ordered.append(value)
    return [v for v in ordered if v]


async def _fetch(coro_factory, what: str) -> List[Any]:
    """Run one upstream fetch, never letting it fail the whole retrieval."""
    try:
        return list(await coro_factory())
    except Exception:
        logger.warning("candidate fetch (%s) failed; continuing", what, exc_info=True)
        return []


async def _from_artist_genre(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
    budget: "_Budget",
) -> None:
    """Gaana's own top tracks for the artists and genres the user leans on.

    The highest-precision source: the user either told us these artists
    (onboarding) or demonstrated them (plays and likes), and Gaana is asked for
    their current catalog rather than for whatever we happened to ingest once.
    """
    from app.services.catalog_service import catalog_service

    artists = _preferred(state.artist_affinity, state.favorite_artists, MAX_ARTIST_FETCHES)
    genres = _preferred(state.genre_affinity, state.favorite_genres, MAX_GENRE_FETCHES)
    languages = _preferred(state.language_affinity, state.preferred_languages, 1)
    language = languages[0] if languages else None

    added = 0
    for artist in artists:
        if budget.spent() or added >= cap:
            return
        for song in await _fetch(
            lambda a=artist: catalog_service.get_artist_top_songs(db, a, limit=PER_FETCH_LIMIT),
            "artist:%s" % artist,
        ):
            if str(song.id) in exclude:
                continue
            cand.add(song, "artist_genre")
            added += 1

    for genre in genres:
        if budget.spent() or added >= cap:
            return
        for song in await _fetch(
            lambda g=genre: catalog_service.get_genre_or_mood_songs(
                db, g, language, limit=PER_FETCH_LIMIT
            ),
            "genre:%s" % genre,
        ):
            if str(song.id) in exclude:
                continue
            cand.add(song, "artist_genre")
            added += 1


async def _from_content(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
    budget: "_Budget",
) -> None:
    """Content kNN against the taste vector, over a Gaana-fetched pool.

    The pool used to be a bounded `select(Song)` prefilter. It is now a live
    Gaana search for the user's strongest mood/genre in their strongest
    language, plus everything the other sources have already pulled in this
    pass -- cosine then reorders within it. Approximate by construction, which
    is what the other sources are for.
    """
    if state.taste_vector is None or linalg.l2_norm(state.taste_vector) <= 1e-12:
        return

    from app.services.catalog_service import catalog_service

    languages = _preferred(state.language_affinity, state.preferred_languages, 1)
    language = languages[0] if languages else None
    terms = _preferred(state.mood_affinity, set(), 1) or _preferred(
        state.genre_affinity, state.favorite_genres, 1
    )

    pool: List[Any] = list(cand.songs.values())
    if terms and not budget.spent():
        pool.extend(
            await _fetch(
                lambda t=terms[0]: catalog_service.get_genre_or_mood_songs(
                    db, t, language, limit=PER_FETCH_LIMIT
                ),
                "content:%s" % terms[0],
            )
        )

    scored: List[Tuple[float, Any, Any]] = []
    seen: Set[str] = set()
    for song in pool:
        song_id = str(song.id)
        if song_id in exclude or song_id in seen:
            continue
        seen.add(song_id)
        vec = cand.vectors.get(song_id) or song_vector(song)
        scored.append((linalg.cosine(state.taste_vector, vec), song, vec))

    scored.sort(key=lambda t: t[0], reverse=True)
    for _, song, vec in scored[:cap]:
        cand.add(song, "content", vector=vec)


async def _from_popular(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
    budget: "_Budget",
) -> None:
    """Gaana's live trending and new releases for the user's languages.

    Unconditional: this is what makes the candidate set non-empty for a user
    with no history at all, and -- unlike the popularity scan it replaces -- it
    reflects what is charting on Gaana right now rather than the play counts
    accumulated in our own database.
    """
    from app.services.catalog_service import catalog_service

    languages = _preferred(
        state.language_affinity, state.preferred_languages, MAX_LANGUAGE_FETCHES
    )
    if not languages:
        # Nothing declared and nothing observed: Gaana's broadest-covered
        # language is the only non-arbitrary starting point for a cold user.
        languages = ["English"]

    added = 0
    for language in languages:
        if budget.spent() or added >= cap:
            return
        for song in await _fetch(
            lambda l=language: catalog_service.get_trending(db, l, limit=PER_FETCH_LIMIT),
            "trending:%s" % language,
        ):
            if str(song.id) in exclude:
                continue
            cand.add(song, "popular")
            added += 1

    if budget.spent() or added >= cap:
        return
    for song in await _fetch(
        lambda: catalog_service.get_new_releases(db, languages[0], limit=PER_FETCH_LIMIT),
        "newreleases:%s" % languages[0],
    ):
        if str(song.id) in exclude:
            continue
        cand.add(song, "popular")
        added += 1


async def _from_cf(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
    budget: "_Budget",
) -> None:
    """Item-item CF neighbours of the user's recent plays.

    Behavioural, not catalogue: the neighbour table is built from real listening
    sessions, so every id it returns is a Gaana track someone actually played.
    Reading those rows back by primary key resolves that reference; it does not
    browse the database for recommendations.

    Silently contributes nothing until a model has been trained.
    """
    from app.ml import item_similarity, registry

    neighbors = await registry.load_item_neighbors(db)
    if not neighbors:
        return

    seeds = state.recent_song_ids[:20]
    if not seeds:
        return

    # Newer plays are stronger seeds; linear decay over the seed list is enough
    # given it is already capped at 20.
    seed_weights = {sid: 1.0 - (i / (len(seeds) * 1.5)) for i, sid in enumerate(seeds)}
    scores = item_similarity.score_candidates(
        neighbors, seeds, seed_weights=seed_weights, exclude=exclude
    )
    if not scores:
        return

    top_ids = [
        sid for sid, _ in sorted(scores.items(), key=lambda p: p[1], reverse=True)[:cap]
        if is_uuid(sid)
    ]
    if not top_ids:
        return

    rows = (await db.execute(select(Song).where(Song.id.in_(top_ids)))).scalars().all()
    for song in rows:
        song_id = str(song.id)
        cand.add(song, "cf")
        cand.cf_scores[song_id] = scores.get(song_id, 0.0)


async def _from_playlists(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
    budget: "_Budget",
) -> None:
    """Songs that share a playlist with something the user liked.

    Behavioural for the same reason as `_from_cf`: the co-occurrence is human
    curation recorded in user data, and the songs it names are Gaana tracks a
    real person put in a real playlist.
    """
    seeds = [s for s in list(state.liked_song_ids)[:50] if is_uuid(s)]
    if not seeds:
        return

    peer_playlists = select(PlaylistSong.playlist_id).where(PlaylistSong.song_id.in_(seeds))
    stmt = (
        select(Song)
        .join(PlaylistSong, PlaylistSong.song_id == Song.id)
        .where(PlaylistSong.playlist_id.in_(peer_playlists))
        .order_by(desc(Song.play_count))
        .limit(cap * 2)
    )
    added = 0
    for song in (await db.execute(stmt)).scalars().unique().all():
        if str(song.id) in exclude:
            continue
        cand.add(song, "playlist")
        added += 1
        if added >= cap:
            break


async def generate(
    db: AsyncSession,
    state: UserState,
    limit: int = config.CANDIDATE_LIMIT,
    exclude_ids: Optional[Set[str]] = None,
    sources: Sequence[str] = config.CANDIDATE_SOURCES,
) -> CandidateSet:
    """Union of the enabled sources, deduplicated and capped.

    Sources run sequentially rather than with `asyncio.gather` because they share
    one `AsyncSession`, which is not safe for concurrent use.
    """
    exclude = set(exclude_ids or set())
    cand = CandidateSet()
    budget = _Budget()

    handlers = {
        "artist_genre": _from_artist_genre,
        "popular": _from_popular,
        "content": _from_content,
        "cf": _from_cf,
        "playlist": _from_playlists,
    }
    # `content` reranks the pool the fetching sources built, so it has to run
    # after them regardless of the order the caller listed the sources in.
    order = {"artist_genre": 0, "popular": 1, "cf": 2, "playlist": 3, "content": 4}
    enabled = sorted({s for s in sources if s in handlers}, key=lambda s: order[s])

    for name in enabled:
        handler = handlers[name]
        cap = config.SOURCE_CAPS.get(name, 100)
        try:
            await handler(db, state, cand, cap, exclude, budget)
        except Exception:
            # One failing source must not empty the feed.
            logger.exception("candidate source %r failed; continuing", name)

    if len(cand) > limit:
        # Deterministic trim: keep the songs the most sources agreed on, then the
        # most popular. Preserves cross-source consensus, which is the strongest
        # cheap signal available before ranking.
        ordered = sorted(
            cand.songs.items(),
            key=lambda kv: (
                -len(cand.sources.get(kv[0], ())),
                -int(getattr(kv[1], "play_count", 0) or 0),
                kv[0],
            ),
        )[:limit]
        keep = {k for k, _ in ordered}
        cand.songs = {k: v for k, v in cand.songs.items() if k in keep}
        cand.sources = defaultdict(set, {k: v for k, v in cand.sources.items() if k in keep})
        cand.vectors = {k: v for k, v in cand.vectors.items() if k in keep}
        cand.cf_scores = {k: v for k, v in cand.cf_scores.items() if k in keep}

    logger.debug(
        "candidates for user=%s: %d songs from %s",
        state.user_id, len(cand), sorted({s for v in cand.sources.values() for s in v}),
    )
    return cand


async def for_seed_song(
    db: AsyncSession,
    seed: Any,
    limit: int = 200,
    exclude_ids: Optional[Set[str]] = None,
) -> CandidateSet:
    """Candidates related to one song -- powers autoplay/radio and similar-songs.

    Seeded by an item rather than a user, so it also works for an anonymous or
    brand-new listener. The content pool is Gaana's own catalog for the seed's
    artist and genre, cosine-reranked against the seed vector; the CF half is
    behavioural, exactly as in `generate`.
    """
    from app.ml import registry
    from app.services.catalog_service import catalog_service

    exclude = set(exclude_ids or set())
    exclude.add(str(getattr(seed, "id", "") or ""))
    cand = CandidateSet()
    seed_vec = song_vector(seed)
    budget = _Budget()

    neighbors = await registry.load_item_neighbors(db)
    seed_id = str(getattr(seed, "id", "") or "")
    cf_pairs = neighbors.get(seed_id, [])
    if cf_pairs:
        ids = [sid for sid, _ in cf_pairs[:limit] if is_uuid(sid) and sid not in exclude]
        if ids:
            peak = max((s for _, s in cf_pairs), default=1.0) or 1.0
            sim_by_id = dict(cf_pairs)
            for song in (await db.execute(select(Song).where(Song.id.in_(ids)))).scalars().all():
                cand.add(song, "cf")
                cand.cf_scores[str(song.id)] = sim_by_id.get(str(song.id), 0.0) / peak

    pool: List[Any] = []
    artist_name = (getattr(seed, "artist_name", "") or "").strip()
    if artist_name and not budget.spent():
        pool.extend(
            await _fetch(
                lambda: catalog_service.get_artist_top_songs(db, artist_name, limit=PER_FETCH_LIMIT),
                "seed-artist:%s" % artist_name,
            )
        )
    genre = (getattr(seed, "genre", "") or "").strip()
    if genre and not budget.spent():
        pool.extend(
            await _fetch(
                lambda: catalog_service.get_genre_or_mood_songs(
                    db, genre, getattr(seed, "language", None), limit=PER_FETCH_LIMIT
                ),
                "seed-genre:%s" % genre,
            )
        )

    scored: List[Tuple[float, Any, Any]] = []
    seen: Set[str] = set()
    for song in pool:
        song_id = str(song.id)
        if song_id in exclude or song_id in seen:
            continue
        seen.add(song_id)
        vec = song_vector(song)
        scored.append((linalg.cosine(seed_vec, vec), song, vec))

    scored.sort(key=lambda t: t[0], reverse=True)
    for _, song, vec in scored[:limit]:
        cand.add(song, "content", vector=vec)

    if len(cand) < limit and not budget.spent():
        # A seed-only "user" so the trending backstop can run unchanged; it
        # reads no personal fields beyond preferred_languages.
        filler_state = UserState(
            user_id="",
            as_of=datetime.now(timezone.utc),
            taste_vector=seed_vec,
            preferred_languages={(getattr(seed, "language", "") or "").strip()} - {""},
        )
        await _from_popular(
            db, filler_state, cand, cap=limit - len(cand), exclude=exclude, budget=budget
        )

    return cand
