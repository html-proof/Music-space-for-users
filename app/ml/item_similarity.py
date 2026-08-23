"""Item-item collaborative filtering from interaction co-occurrence.

Two songs are similar if the same people engage with both. That signal is
orthogonal to content similarity -- it captures "people who like this also like
that" across genre and language boundaries that `features.song_vector` cannot
cross -- which is why both feed the ranker as separate features.

Co-occurrence is counted within *baskets*, not across a user's whole history: two
songs played in the same sitting are related, two songs played four months apart
mostly just mean the user is active. A basket is a listening session (the
`session_id` column when present, otherwise a gap-based split at
`CF_MAX_SESSION_SPAN_HOURS`), plus one basket per user of everything they liked.

Similarity is shrunk cosine:

    sim(i,j) = cooc(i,j) / sqrt(n_i * n_j) * cooc(i,j) / (cooc(i,j) + SHRINKAGE)

The second factor is doing real work at this data volume. Raw cosine gives a
pair that co-occurred exactly once in two singleton baskets a perfect 1.0 --
which would outrank a pair with 200 co-occurrences. Shrinkage costs a
well-supported pair almost nothing (200/210) and crushes the singleton (1/11).
"""
import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import is_uuid
from app.models.history import ListeningHistory
from app.models.song import LikedSong
from app.ml import config
from app.ml.features import _as_utc

logger = logging.getLogger("ml.item_similarity")

# A basket bigger than this is almost certainly a bulk import or a bot, and it
# would contribute O(n^2) pairs -- 500 songs is 125k pairs from one row group.
MAX_BASKET_SIZE = 60


async def collect_baskets(
    db: AsyncSession,
    max_rows: int = 200_000,
) -> List[List[str]]:
    """Co-occurrence baskets over the whole catalog.

    Skips are excluded: a skip says the pairing was *wrong*, so counting it as
    co-occurrence would teach the model the opposite of what happened.
    """
    stmt = (
        select(
            ListeningHistory.user_id,
            ListeningHistory.session_id,
            ListeningHistory.song_id,
            ListeningHistory.started_at,
        )
        .where(ListeningHistory.skipped.is_(False))
        .order_by(ListeningHistory.user_id, ListeningHistory.started_at)
        .limit(max_rows)
    )
    rows = (await db.execute(stmt)).all()

    baskets: List[List[str]] = []
    # (user, session) keyed groups for rows that carry a session id.
    keyed: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    # Gap-split groups for rows that do not.
    current: List[str] = []
    current_user: Optional[str] = None
    last_at: Optional[datetime] = None
    span = config.CF_MAX_SESSION_SPAN_HOURS * 3600.0

    def flush():
        nonlocal current
        if len(current) > 1:
            baskets.append(current[:MAX_BASKET_SIZE])
        current = []

    for user_id, session_id, song_id, started_at in rows:
        uid, sid = str(user_id), str(song_id)
        if session_id:
            keyed[(uid, str(session_id))].append(sid)
            continue

        at = _as_utc(started_at)
        gap_too_big = (
            last_at is None or at is None or (at - last_at).total_seconds() > span
        )
        if uid != current_user or gap_too_big:
            flush()
            current_user = uid
        current.append(sid)
        last_at = at
    flush()

    for songs in keyed.values():
        if len(songs) > 1:
            baskets.append(songs[:MAX_BASKET_SIZE])

    # One basket per user of their likes: an explicit statement that these songs
    # belong together, and often the only signal a low-activity user produces.
    liked_stmt = select(LikedSong.user_id, LikedSong.song_id).order_by(LikedSong.user_id)
    liked: Dict[str, List[str]] = defaultdict(list)
    for user_id, song_id in (await db.execute(liked_stmt)).all():
        liked[str(user_id)].append(str(song_id))
    for songs in liked.values():
        if len(songs) > 1:
            baskets.append(songs[:MAX_BASKET_SIZE])

    return baskets


def build_neighbors(
    baskets: Iterable[Sequence[str]],
    top_k: int = config.CF_TOP_K,
    shrinkage: float = config.CF_SHRINKAGE,
) -> Dict[str, List[Tuple[str, float]]]:
    """Shrunk-cosine top-K neighbours per song.

    Pure function of the baskets, so it is directly unit-testable on a
    hand-built co-occurrence set without touching a database.
    """
    counts: Dict[str, int] = defaultdict(int)
    cooc: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for basket in baskets:
        # Within-basket repeats must not inflate the pair count, and a song
        # cannot co-occur with itself.
        unique = list(dict.fromkeys(str(s) for s in basket))
        if len(unique) < 2:
            continue
        for song in unique:
            counts[song] += 1
        for i in range(len(unique)):
            a = unique[i]
            for j in range(i + 1, len(unique)):
                b = unique[j]
                cooc[a][b] += 1
                cooc[b][a] += 1

    neighbors: Dict[str, List[Tuple[str, float]]] = {}
    for song, partners in cooc.items():
        n_a = counts.get(song, 0)
        if n_a <= 0:
            continue
        scored: List[Tuple[str, float]] = []
        for other, c in partners.items():
            n_b = counts.get(other, 0)
            if n_b <= 0 or c <= 0:
                continue
            cosine = c / math.sqrt(n_a * n_b)
            sim = cosine * (c / (c + shrinkage))
            if sim > 0.0:
                scored.append((other, sim))
        if not scored:
            continue
        scored.sort(key=lambda p: p[1], reverse=True)
        neighbors[song] = scored[:top_k]

    return neighbors


async def build_from_db(db: AsyncSession) -> Tuple[Dict[str, List[Tuple[str, float]]], Dict[str, int]]:
    """(neighbours, stats) for the whole catalog."""
    baskets = await collect_baskets(db)
    neighbors = build_neighbors(baskets)
    stats = {
        "n_baskets": len(baskets),
        "n_songs": len(neighbors),
        "n_pairs": sum(len(v) for v in neighbors.values()),
    }
    logger.info("item_sim built: %s", stats)
    return neighbors, stats


def score_candidates(
    neighbors: Dict[str, List[Tuple[str, float]]],
    seed_song_ids: Sequence[str],
    seed_weights: Optional[Dict[str, float]] = None,
    exclude: Optional[Set[str]] = None,
) -> Dict[str, float]:
    """Aggregate CF score per candidate, given the songs the user just played.

    Scores are normalised by the maximum so `cf_score` stays in [0, 1] and stays
    comparable between a user with 3 seeds and one with 20 -- the ranker has a
    single coefficient for both.
    """
    if not neighbors or not seed_song_ids:
        return {}

    exclude = exclude or set()
    totals: Dict[str, float] = defaultdict(float)
    for seed in seed_song_ids:
        seed = str(seed)
        weight = (seed_weights or {}).get(seed, 1.0)
        for other, sim in neighbors.get(seed, ()):
            if other in exclude or other == seed:
                continue
            totals[other] += sim * weight

    if not totals:
        return {}
    peak = max(totals.values()) or 1.0
    return {song_id: value / peak for song_id, value in totals.items()}


async def neighbors_for_song(
    db: AsyncSession,
    song_id: str,
    limit: int = 20,
) -> List[Tuple[str, float]]:
    """CF neighbours of one song, from the active artifact.

    Empty when no model has been trained, which is the expected state early on;
    `recommendation_service.get_similar_songs` blends this with content kNN
    rather than depending on it.
    """
    from app.ml import registry

    if not is_uuid(song_id):
        return []
    all_neighbors = await registry.load_item_neighbors(db)
    return all_neighbors.get(str(song_id), [])[:limit]


async def recent_seed_songs(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
) -> List[str]:
    """The user's most recent non-skipped plays, newest first."""
    if not is_uuid(user_id):
        return []
    stmt = (
        select(ListeningHistory.song_id)
        .where(
            ListeningHistory.user_id == user_id,
            ListeningHistory.skipped.is_(False),
        )
        .order_by(desc(ListeningHistory.started_at))
        .limit(limit)
    )
    seen: List[str] = []
    for (song_id,) in (await db.execute(stmt)).all():
        sid = str(song_id)
        if sid not in seen:
            seen.append(sid)
    return seen
