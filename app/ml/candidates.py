"""Candidate generation -- the retrieval stage.

Ranking every song in the catalog for every request does not scale, so retrieval
narrows the field to a few hundred plausible candidates cheaply, and only those
get the full feature treatment. With 20 songs in the database today every source
returns the whole catalog and this layer is a no-op; it is written for the shape
the catalog will have after `ingest_catalog.py` runs, and the caps are what stop
a 100k-song catalog from turning the home feed into a table scan.

Five sources, each capped and each tagging its output. Provenance is itself a
ranking feature (`src_*`), so the ranker can learn that, say, CF candidates
convert better than popularity ones for a given user population -- something a
hand-tuned blend cannot discover.

The union always includes popularity, which guarantees a non-empty candidate set
for a brand-new user with no history and no declared preferences.
"""
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select, or_, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import is_uuid
from app.models.song import Song
from app.models.playlist import PlaylistSong
from app.ml import config, linalg
from app.ml.features import UserState, song_vector

logger = logging.getLogger("ml.candidates")

# Upper bound on the content-kNN prefilter scan. Cosine is computed in Python, so
# this is the real cost knob: 2000 songs x 256 dims is a few milliseconds under
# numpy and stays acceptable on the fallback path.
CONTENT_SCAN_LIMIT = 2000


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


async def _from_content(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
) -> None:
    """Content kNN against the taste vector.

    A SQL prefilter on the user's strongest languages/genres/artists bounds the
    scan; exact cosine then reorders within it. This is approximate by
    construction -- a song in a language the user has never touched cannot be
    retrieved here -- which is precisely what the other four sources are for.
    """
    if state.taste_vector is None or linalg.l2_norm(state.taste_vector) <= 1e-12:
        return

    def _top(scores: Dict[str, float], n: int) -> List[str]:
        return [k for k, _ in sorted(scores.items(), key=lambda p: p[1], reverse=True)[:n]]

    langs = _top(state.language_affinity, 3) or list(state.preferred_languages)[:3]
    genres = _top(state.genre_affinity, 5) or list(state.favorite_genres)[:5]
    artists = _top(state.artist_affinity, 8)

    filters = []
    if langs:
        filters.append(func.lower(Song.language).in_([l.lower() for l in langs]))
    if genres:
        filters.append(func.lower(Song.genre).in_([g.lower() for g in genres]))
    if artists:
        filters.append(func.lower(Song.artist_name).in_([a.lower() for a in artists]))

    stmt = select(Song)
    if filters:
        stmt = stmt.where(or_(*filters))
    stmt = stmt.order_by(desc(Song.play_count)).limit(CONTENT_SCAN_LIMIT)

    rows = list((await db.execute(stmt)).scalars().all())
    scored: List[Tuple[float, Any, Any]] = []
    for song in rows:
        song_id = str(song.id)
        if song_id in exclude:
            continue
        vec = cand.vectors.get(song_id) or song_vector(song)
        scored.append((linalg.cosine(state.taste_vector, vec), song, vec))

    scored.sort(key=lambda t: t[0], reverse=True)
    for _, song, vec in scored[:cap]:
        cand.add(song, "content", vector=vec)


async def _from_cf(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
) -> None:
    """Item-item CF neighbours of the user's recent plays.

    Silently contributes nothing until a model has been trained, which is the
    expected state on a fresh database.
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


async def _from_artist_genre(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
) -> None:
    """Straight SQL expansion on the user's top artists and genres.

    Cheap, high-precision, and the source that carries the load while the taste
    vector is still thin.
    """
    def _top(scores: Dict[str, float], n: int) -> List[str]:
        return [k for k, _ in sorted(scores.items(), key=lambda p: p[1], reverse=True)[:n]]

    artists = _top(state.artist_affinity, 5)
    genres = _top(state.genre_affinity, 3) or list(state.favorite_genres)[:3]
    if not artists and not genres:
        return

    filters = []
    if artists:
        filters.append(func.lower(Song.artist_name).in_([a.lower() for a in artists]))
    if genres:
        filters.append(func.lower(Song.genre).in_([g.lower() for g in genres]))

    stmt = select(Song).where(or_(*filters)).order_by(desc(Song.play_count)).limit(cap * 2)
    for song in (await db.execute(stmt)).scalars().all():
        if str(song.id) not in exclude:
            cand.add(song, "artist_genre")


async def _from_popular(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
) -> None:
    """Popularity backstop. Language-filtered when known, unfiltered otherwise.

    This source is unconditional: it is what makes the candidate set non-empty
    for a user with no history at all.
    """
    langs = [l for l in state.preferred_languages if l]
    stmt = select(Song)
    if langs:
        stmt = stmt.where(func.lower(Song.language).in_([l.lower() for l in langs]))
    stmt = stmt.order_by(desc(Song.play_count)).limit(cap * 2)

    rows = list((await db.execute(stmt)).scalars().all())
    if not rows and langs:
        # Preferred language has no catalog coverage yet -- fall back to global
        # popularity rather than returning an empty feed.
        rows = list(
            (await db.execute(select(Song).order_by(desc(Song.play_count)).limit(cap * 2)))
            .scalars().all()
        )

    added = 0
    for song in rows:
        if str(song.id) in exclude:
            continue
        cand.add(song, "popular")
        added += 1
        if added >= cap:
            break


async def _from_playlists(
    db: AsyncSession,
    state: UserState,
    cand: CandidateSet,
    cap: int,
    exclude: Set[str],
) -> None:
    """Songs that share a playlist with something the user liked.

    Human-curated co-occurrence, and unlike CF it works with a single like
    because the curation was done by someone else.
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

    handlers = {
        "content": _from_content,
        "cf": _from_cf,
        "artist_genre": _from_artist_genre,
        "popular": _from_popular,
        "playlist": _from_playlists,
    }

    for name in sources:
        handler = handlers.get(name)
        if handler is None:
            continue
        cap = config.SOURCE_CAPS.get(name, 100)
        try:
            await handler(db, state, cand, cap, exclude)
        except Exception:
            # One failing source must not empty the feed; popularity almost
            # always survives and is enough to render something.
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

    Unlike `generate`, this is seeded by an item rather than a user, so it also
    works for an anonymous or brand-new listener.
    """
    from app.ml import item_similarity, registry

    exclude = set(exclude_ids or set())
    exclude.add(str(getattr(seed, "id", "") or ""))
    cand = CandidateSet()
    seed_vec = song_vector(seed)

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

    filters = []
    if getattr(seed, "artist_name", None):
        filters.append(func.lower(Song.artist_name) == seed.artist_name.strip().lower())
    if getattr(seed, "genre", None):
        filters.append(func.lower(Song.genre) == seed.genre.strip().lower())
    if getattr(seed, "language", None):
        filters.append(func.lower(Song.language) == seed.language.strip().lower())

    stmt = select(Song)
    if filters:
        stmt = stmt.where(or_(*filters))
    stmt = stmt.order_by(desc(Song.play_count)).limit(CONTENT_SCAN_LIMIT)

    scored: List[Tuple[float, Any, Any]] = []
    for song in (await db.execute(stmt)).scalars().all():
        song_id = str(song.id)
        if song_id in exclude:
            continue
        vec = song_vector(song)
        scored.append((linalg.cosine(seed_vec, vec), song, vec))

    scored.sort(key=lambda t: t[0], reverse=True)
    for _, song, vec in scored[:limit]:
        cand.add(song, "content", vector=vec)

    if len(cand) < limit:
        # A seed-only "user" so the popularity backstop can run unchanged; it
        # reads no personal fields beyond preferred_languages, which is empty here.
        filler_state = UserState(
            user_id="", as_of=datetime.now(timezone.utc), taste_vector=seed_vec
        )
        await _from_popular(db, filler_state, cand, cap=limit - len(cand), exclude=exclude)

    return cand
