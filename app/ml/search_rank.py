"""Search re-ranking: lexical relevance blended with personalization.

Search is not recommendation. A user who types an exact title wants *that title*,
even by an artist they have never played -- so lexical relevance carries a weight
3x personalization (`config.SEARCH_LEXICAL_WEIGHT`). Personalization is the
tie-breaker among comparably-matching results, which is where it belongs and
where it actually helps: "Believer" by their favourite artist above "Believer" by
a stranger.

Lexical scoring is a tiered cascade -- exact, prefix, all-tokens, some-tokens,
fuzzy -- because the tiers are ordered by how much they tell you, and a single
blended similarity would let a strong fuzzy match outrank a weak exact one. The
implementation is deliberately dependency-free: Postgres `pg_trgm` would be
better, but it needs an extension and an index migration, and the Gaana
passthrough results are not in Postgres at all.
"""
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.ml import config, linalg
from app.ml.features import UserState, song_vector

logger = logging.getLogger("ml.search_rank")

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Beyond this, edit distance stops being "a typo" and starts being "a different
# word", and the bounded DP below can bail out early.
MAX_EDIT_DISTANCE = 2

# A flat MAX_EDIT_DISTANCE=2 tolerance, gated only at len(token) >= 4, let a
# short word fuzzy-match an entirely different short word: "sakar" is exactly
# 1 edit from "safar" (k->f), so it fuzzy-matched and outranked the real
# "Sakar" result; "pattalam" is exactly 2 edits from "pattellam", a different
# word, not a typo of it. Below MIN_FUZZY_TOKEN_LENGTH a token skips fuzzy
# matching entirely -- on a short word there are too many unrelated real
# words within 1-2 edits for that distance to mean "probably a typo". Longer
# tokens still get a length-scaled tolerance.
MIN_FUZZY_TOKEN_LENGTH = 7


def _fuzzy_tolerance(token_length: int) -> int:
    if token_length <= 8:
        return 1
    return MAX_EDIT_DISTANCE


def _norm(text: Optional[str]) -> str:
    return (text or "").strip().lower()


def _tokens(text: Optional[str]) -> List[str]:
    return _TOKEN_RE.findall(_norm(text))


def bounded_edit_distance(a: str, b: str, max_distance: int = MAX_EDIT_DISTANCE) -> int:
    """Levenshtein distance, capped at `max_distance` (returns max+1 when over).

    Capping turns the usual O(len(a)*len(b)) into something closer to
    O(len(a)*max_distance), which matters when this runs against a few hundred
    candidate titles per keystroke on the autocomplete path.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_distance:
        return max_distance + 1
    if not a:
        return len(b) if len(b) <= max_distance else max_distance + 1
    if not b:
        return len(a) if len(a) <= max_distance else max_distance + 1

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        lo = max(1, i - max_distance)
        hi = min(len(b), i + max_distance)
        for j in range(1, len(b) + 1):
            if j < lo or j > hi:
                current[j] = max_distance + 1
                continue
            cost = 0 if ca == b[j - 1] else 1
            current[j] = min(
                previous[j] + 1,        # deletion
                current[j - 1] + 1,     # insertion
                previous[j - 1] + cost, # substitution
            )
        if min(current[lo:hi + 1] or [max_distance + 1]) > max_distance:
            return max_distance + 1
        previous = current

    return previous[len(b)] if previous[len(b)] <= max_distance else max_distance + 1


def field_lexical_score(query: str, value: Optional[str]) -> float:
    """Tiered lexical match of `query` against one field, in [0, 1]."""
    q, v = _norm(query), _norm(value)
    if not q or not v:
        return 0.0

    if q == v:
        return 1.0
    if v.startswith(q):
        # Longer prefixes of a short field are stronger evidence: "believ" against
        # "Believer" beats "believ" against "Believer (Remix) [Extended]".
        return 0.85 + 0.10 * (len(q) / len(v))
    if q in v:
        return 0.70 * (len(q) / len(v)) + 0.10

    q_tokens, v_tokens = _tokens(q), set(_tokens(v))
    if not q_tokens:
        return 0.0

    matched = 0
    fuzzy_matched = 0
    for token in q_tokens:
        if token in v_tokens:
            matched += 1
            continue
        if any(vt.startswith(token) for vt in v_tokens):
            matched += 1
            continue
        tolerance = _fuzzy_tolerance(len(token))
        if len(token) >= MIN_FUZZY_TOKEN_LENGTH and any(
            bounded_edit_distance(token, vt, tolerance) <= tolerance for vt in v_tokens
        ):
            fuzzy_matched += 1

    if matched == len(q_tokens):
        return 0.65
    covered = (matched + 0.5 * fuzzy_matched) / len(q_tokens)
    return 0.55 * covered if covered > 0 else 0.0


def lexical_score(query: str, song: Any) -> float:
    """Best-field lexical relevance, with a small bonus for matching two fields.

    Max rather than sum across fields: a query is normally *about* one field, and
    summing lets a song whose title and album and artist all weakly contain the
    query outrank an exact title match. The bonus exists for the "artist + title"
    query shape ("dua lipa levitating"), which is common enough to be worth it.
    """
    title = field_lexical_score(query, getattr(song, "title", None))
    artist = field_lexical_score(query, getattr(song, "artist_name", None))
    album = field_lexical_score(query, getattr(song, "album_name", None))

    best = max(title, artist, album)
    second = sorted((title, artist, album), reverse=True)[1]
    return min(best + 0.15 * second, 1.0)


@dataclass
class SearchResult:
    song: Any
    score: float
    lexical: float
    personal: float
    popularity: float

    @property
    def song_id(self) -> str:
        return str(getattr(self.song, "id", "") or "")


def personal_score(song: Any, state: Optional[UserState], song_vec: Any = None) -> float:
    """Affinity of one song to a user, in roughly [0, 1].

    Kept simpler than the full ranking feature vector on purpose: the ranker's
    features include `recency_decay` and `novelty`, which *penalise* recently
    played songs. That is right for a feed and wrong for search -- someone
    searching for a song they played an hour ago wants to find it.
    """
    if state is None or not state.has_signal():
        return 0.0

    vec = song_vec if song_vec is not None else song_vector(song)
    content = max(0.0, linalg.cosine(state.taste_vector, vec)) if state.taste_vector is not None else 0.0

    artist = _norm(getattr(song, "artist_name", None))
    peak = max(state.artist_affinity.values(), default=0.0) or 1.0
    artist_affinity = max(0.0, min(state.artist_affinity.get(artist, 0.0) / peak, 1.0))

    song_id = str(getattr(song, "id", "") or "")
    liked = 1.0 if song_id in state.liked_song_ids else 0.0
    played = 1.0 if state.play_counts.get(song_id, 0) > 0 else 0.0
    language = _norm(getattr(song, "language", None))
    lang_match = 1.0 if language and language in state.preferred_languages else 0.0

    return min(
        0.40 * content + 0.25 * artist_affinity + 0.15 * liked
        + 0.10 * played + 0.10 * lang_match,
        1.0,
    )


def rank(
    query: str,
    songs: Iterable[Any],
    state: Optional[UserState] = None,
    limit: Optional[int] = None,
    max_play_count: int = 1,
    min_lexical: float = 0.0,
) -> List[SearchResult]:
    """Re-rank search results by lexical relevance, personalization and popularity.

    Order within the returned list is stable for equal scores (by title), so
    identical queries do not shuffle results between requests.
    """
    q = _norm(query)
    results: List[SearchResult] = []
    max_play = max(int(max_play_count or 1), 1)

    for song in songs:
        lex = lexical_score(q, song) if q else 0.0
        if q and lex <= min_lexical:
            continue
        vec = song_vector(song)
        per = personal_score(song, state, song_vec=vec)
        pop = math.log1p(int(getattr(song, "play_count", 0) or 0)) / math.log1p(max_play)
        score = (
            config.SEARCH_LEXICAL_WEIGHT * lex
            + config.SEARCH_PERSONAL_WEIGHT * per
            + config.SEARCH_POPULARITY_WEIGHT * pop
        )
        results.append(SearchResult(song=song, score=score, lexical=lex, personal=per, popularity=pop))

    results.sort(key=lambda r: (-r.score, _norm(getattr(r.song, "title", ""))))
    return results[:limit] if limit else results


def suggest(
    prefix: str,
    songs: Iterable[Any],
    state: Optional[UserState] = None,
    limit: int = 10,
    recent_queries: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Autocomplete suggestions for a partial query.

    Deduplicated by display text, because "Believer" appearing three times as
    title, album and artist is a worse suggestion list than three distinct ideas.
    The user's own recent searches are surfaced first when they match -- the
    cheapest high-precision suggestion source there is.
    """
    q = _norm(prefix)
    if not q:
        return []

    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for recent in (recent_queries or ()):
        r = _norm(recent)
        if r and r.startswith(q) and r not in seen:
            seen.add(r)
            out.append({"text": recent, "type": "recent_search", "score": 1.0})
            if len(out) >= limit:
                return out

    ranked = rank(q, songs, state=state, min_lexical=0.0)
    for result in ranked:
        song = result.song
        for value, kind in (
            (getattr(song, "title", None), "song"),
            (getattr(song, "artist_name", None), "artist"),
        ):
            key = _norm(value)
            if not key or key in seen:
                continue
            if field_lexical_score(q, value) < 0.55:
                continue
            seen.add(key)
            out.append({
                "text": value,
                "type": kind,
                "score": round(result.score, 4),
                "song_id": result.song_id if kind == "song" else None,
                "thumbnail_url": getattr(song, "thumbnail_url", None) if kind == "song" else None,
            })
            if len(out) >= limit:
                return out

    return out
