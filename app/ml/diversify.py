"""Post-processing: diversity, artist caps, and exploration.

A ranker maximises per-item relevance, which on its own produces a shelf of ten
near-identical songs -- often the same artist, because whatever made the top hit
score well makes its neighbours score well too. Ranking quality and *list*
quality are different objectives, so the list-level constraints live here rather
than being smuggled into the scoring function.

Three passes, in order:
  1. MMR re-rank -- trade a little relevance for intra-list variety
  2. artist cap  -- hard ceiling per artist per shelf
  3. exploration -- swap in a small fraction of unheard songs

Order matters: capping before MMR would make MMR optimise over an already
truncated list, and exploring before capping could reintroduce a capped artist.
"""
import logging
import random
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from app.ml import config, linalg
from app.ml.ranker import ScoredSong

logger = logging.getLogger("ml.diversify")


def _artist_key(scored: ScoredSong) -> str:
    song = scored.song
    artist_id = getattr(song, "artist_id", None)
    if artist_id:
        return f"id:{artist_id}"
    return "name:" + (getattr(song, "artist_name", "") or "").strip().lower()


def mmr_rerank(
    candidates: Sequence[ScoredSong],
    limit: int,
    lambda_: float = config.MMR_LAMBDA,
) -> List[ScoredSong]:
    """Maximal Marginal Relevance over content vectors.

    Greedily picks the candidate maximising

        lambda * relevance - (1 - lambda) * max_similarity_to_already_picked

    Cost is O(limit * len(candidates) * dim), so callers should pass a candidate
    list already truncated to a few hundred -- which `candidates.py` does.
    """
    if lambda_ >= 1.0 or limit <= 0 or not candidates:
        return list(candidates[:limit])

    pool = list(candidates)
    selected: List[ScoredSong] = [pool.pop(0)]  # highest relevance always leads

    while pool and len(selected) < limit:
        best_idx, best_value = 0, float("-inf")
        for idx, cand in enumerate(pool):
            if cand.vector is None:
                penalty = 0.0
            else:
                penalty = max(
                    (
                        linalg.cosine(cand.vector, s.vector)
                        for s in selected
                        if s.vector is not None
                    ),
                    default=0.0,
                )
            value = lambda_ * cand.score - (1.0 - lambda_) * penalty
            if value > best_value:
                best_idx, best_value = idx, value
        selected.append(pool.pop(best_idx))

    return selected


def cap_per_artist(
    candidates: Iterable[ScoredSong],
    limit: int,
    max_per_artist: int = config.MAX_PER_ARTIST,
) -> List[ScoredSong]:
    """Keep at most `max_per_artist` songs per artist, preserving order.

    If the cap leaves the shelf short -- a real case when the catalog holds 20
    songs by 6 artists -- the overflow is added back at the end rather than
    returning a half-empty shelf. A slightly repetitive shelf beats a stub.
    """
    kept: List[ScoredSong] = []
    overflow: List[ScoredSong] = []
    counts: Dict[str, int] = {}

    for cand in candidates:
        key = _artist_key(cand)
        if counts.get(key, 0) < max_per_artist:
            counts[key] = counts.get(key, 0) + 1
            kept.append(cand)
            if len(kept) >= limit:
                return kept
        else:
            overflow.append(cand)

    if len(kept) < limit and overflow:
        kept.extend(overflow[: limit - len(kept)])
    return kept


def inject_exploration(
    selected: List[ScoredSong],
    pool: Sequence[ScoredSong],
    played_song_ids: Set[str],
    rate: float = config.EXPLORATION_RATE,
    rng: Optional[random.Random] = None,
) -> List[ScoredSong]:
    """Replace a `rate` fraction of tail slots with unheard candidates.

    Pure exploitation makes the feed converge on the user's existing taste and
    stop showing them anything new, so the ranker never gets evidence about
    songs it scores low. Swaps happen at the *tail* so the top of the shelf stays
    the model's best guess, and only ever replace already-heard songs.
    """
    if rate <= 0.0 or not selected or not pool:
        return selected

    n_swap = int(len(selected) * rate)
    if n_swap <= 0:
        return selected

    rng = rng or random.Random()
    chosen_ids = {s.song_id for s in selected}
    unheard = [
        c for c in pool
        if c.song_id not in chosen_ids and c.song_id not in played_song_ids
    ]
    if not unheard:
        return selected

    rng.shuffle(unheard)
    out = list(selected)
    swapped = 0
    for i in range(len(out) - 1, -1, -1):
        if swapped >= n_swap or not unheard:
            break
        if out[i].song_id in played_song_ids:
            out[i] = unheard.pop()
            swapped += 1

    return out


def finalize(
    candidates: Sequence[ScoredSong],
    limit: int,
    played_song_ids: Optional[Set[str]] = None,
    exclude_ids: Optional[Set[str]] = None,
    max_per_artist: int = config.MAX_PER_ARTIST,
    mmr_lambda: float = config.MMR_LAMBDA,
    exploration_rate: float = config.EXPLORATION_RATE,
    rng: Optional[random.Random] = None,
) -> List[ScoredSong]:
    """Full post-processing pipeline: exclude -> MMR -> cap -> explore."""
    exclude_ids = exclude_ids or set()
    pool = [c for c in candidates if c.song_id and c.song_id not in exclude_ids]
    if not pool:
        return []

    # Over-fetch so the artist cap has slack to drop from without shortening the
    # shelf, then trim to `limit` after capping.
    diversified = mmr_rerank(pool, limit=min(len(pool), max(limit * 3, limit)), lambda_=mmr_lambda)
    capped = cap_per_artist(diversified, limit=limit, max_per_artist=max_per_artist)
    return inject_exploration(
        capped, pool, played_song_ids or set(), rate=exploration_rate, rng=rng
    )
