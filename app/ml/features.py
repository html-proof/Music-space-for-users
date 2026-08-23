"""Song content vectors, user taste vectors, and the ranking feature builder.

Two design points that the rest of the pipeline depends on:

1. `song_vector` is a pure, deterministic function of a Song row. Nothing is
   stored or trained, so a song upserted from Gaana one second ago is as
   rankable as one played a thousand times. That removes item cold start
   entirely, which matters here because the catalog is refilled continuously by
   `catalog_service.upsert_gaana_song`.

2. `build_ranking_features` takes an explicit `UserState` snapshot rather than a
   db session. Training builds that snapshot from interactions strictly before
   the row's timestamp; serving builds it from everything up to now. Sharing one
   feature function between the two is what guarantees train/serve parity, and
   passing state in (instead of querying inside) is what makes the no-leakage
   guarantee checkable -- see tests/test_ml.py.
"""
import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from app.ml import config
from app.ml import linalg

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Title noise that carries no taste information and would otherwise pull
# unrelated songs together through the "remix"/"lofi" token.
_TITLE_STOPWORDS = frozenset({
    "remix", "version", "live", "lofi", "slowed", "reverb", "mix", "edit",
    "feat", "ft", "official", "audio", "video", "song", "theme", "title",
    "from", "the", "a", "an", "and", "of", "in", "on", "to",
})


def _tokens(text: Optional[str]) -> List[str]:
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def _hash_index(field_name: str, value: str) -> int:
    """Stable feature index for a field/value pair.

    blake2b rather than the builtin hash(): PYTHONHASHSEED randomises str
    hashing per process, which would give a song a different vector on every
    restart and silently invalidate every persisted similarity.
    """
    digest = hashlib.blake2b(
        f"{field_name}\x00{value}".encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big")


def song_feature_pairs(song: Any) -> List[tuple]:
    """(hash index, weight) pairs describing a song's content."""
    pairs: List[tuple] = []
    w = config.FIELD_WEIGHTS

    for field_name, weight_key, raw in (
        ("artist", "artist", getattr(song, "artist_name", None)),
        ("genre", "genre", getattr(song, "genre", None)),
        ("language", "language", getattr(song, "language", None)),
        ("mood", "mood", getattr(song, "mood", None)),
        ("album", "album", getattr(song, "album_name", None)),
    ):
        if raw:
            value = str(raw).strip().lower()
            if value:
                pairs.append((_hash_index(field_name, value), w[weight_key]))

    seen: Set[str] = set()
    for token in _tokens(getattr(song, "title", None)):
        if token in _TITLE_STOPWORDS or len(token) < 2 or token in seen:
            continue
        seen.add(token)
        pairs.append((_hash_index("title", token), w["title_token"]))

    return pairs


def song_vector(song: Any, dim: int = config.EMBEDDING_DIM):
    """Unit-length content vector for a song.

    Unit length means a dot product between two of these is already a cosine,
    which is what `content_cos` and the similar-songs kNN both consume.
    """
    return linalg.normalize(linalg.from_pairs(dim, song_feature_pairs(song)))


def preference_vector(
    preferred_languages: Sequence[str],
    favorite_genres: Sequence[str],
    favorite_artists: Sequence[str],
    favorite_moods: Sequence[str],
    dim: int = config.EMBEDDING_DIM,
):
    """Cold-start taste vector built from explicitly declared preferences.

    Without this a user who has just finished onboarding but played nothing has a
    zero taste vector, `content_cos` is 0 for every candidate, and the feed
    collapses to pure popularity. Their stated preferences are weak evidence but
    they are evidence.
    """
    pairs: List[tuple] = []
    w = config.FIELD_WEIGHTS
    for lang in preferred_languages or ():
        if lang:
            pairs.append((_hash_index("language", str(lang).strip().lower()), w["language"]))
    for genre in favorite_genres or ():
        if genre:
            pairs.append((_hash_index("genre", str(genre).strip().lower()), w["genre"]))
    for artist in favorite_artists or ():
        if artist:
            pairs.append((_hash_index("artist", str(artist).strip().lower()), w["artist"]))
    for mood in favorite_moods or ():
        if mood:
            pairs.append((_hash_index("mood", str(mood).strip().lower()), w["mood"]))
    return linalg.normalize(linalg.from_pairs(dim, pairs))


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Coerce to timezone-aware UTC.

    SQLite hands back naive datetimes for the same column Postgres returns as
    timestamptz, so every time arithmetic here would raise on one backend or the
    other without this.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def time_decay(then: Optional[datetime], now: datetime, halflife_days: float) -> float:
    """Exponential recency weight in (0, 1]. Missing timestamps count as fully decayed."""
    then = _as_utc(then)
    if then is None or halflife_days <= 0:
        return 1.0 if then is not None else 0.0
    age_days = max((now - then).total_seconds() / 86400.0, 0.0)
    return math.pow(0.5, age_days / halflife_days)


@dataclass
class Interaction:
    """One engagement event, normalised across the tables it can come from."""
    song_id: str
    kind: str                       # play | complete | skip | like | playlist_add
    at: Optional[datetime]
    completion: float = 0.0
    artist_name: str = ""
    genre: Optional[str] = None
    language: Optional[str] = None
    mood: Optional[str] = None

    @property
    def weight(self) -> float:
        base = config.SIGNAL_WEIGHTS.get(self.kind, 1.0)
        if self.kind in ("play", "complete"):
            # A 90%-finished play is much stronger evidence than a 15% one.
            base *= 0.5 + (max(0.0, min(self.completion, 100.0)) / 100.0)
        return base


@dataclass
class UserState:
    """Everything the ranker knows about a user at one point in time.

    Built by `user_state.build_user_state` for serving and by the training
    dataset builder for each historical row. Every field is an aggregate over
    interactions *before* `as_of`; nothing here may be derived from the row being
    predicted.
    """
    user_id: str
    as_of: datetime
    taste_vector: Any
    artist_affinity: Dict[str, float] = field(default_factory=dict)
    genre_affinity: Dict[str, float] = field(default_factory=dict)
    language_affinity: Dict[str, float] = field(default_factory=dict)
    mood_affinity: Dict[str, float] = field(default_factory=dict)
    play_counts: Dict[str, int] = field(default_factory=dict)
    last_played_at: Dict[str, datetime] = field(default_factory=dict)
    liked_song_ids: Set[str] = field(default_factory=set)
    playlist_song_ids: Set[str] = field(default_factory=set)
    followed_artists: Set[str] = field(default_factory=set)
    saved_album_ids: Set[str] = field(default_factory=set)
    preferred_languages: Set[str] = field(default_factory=set)
    favorite_genres: Set[str] = field(default_factory=set)
    favorite_artists: Set[str] = field(default_factory=set)
    allows_explicit: bool = True
    mean_duration: float = 0.0
    recent_song_ids: List[str] = field(default_factory=list)
    n_interactions: int = 0

    def has_signal(self) -> bool:
        """True once there is enough history for personalization to mean anything."""
        return self.n_interactions > 0


def _normalized_affinity(scores: Dict[str, float], key: Optional[str]) -> float:
    """Affinity for `key`, scaled to roughly [-1, 1] by the strongest affinity.

    Dividing by the max keeps the feature on a comparable scale between a user
    with 5 plays and one with 5000, which a linear model needs since it has a
    single weight for both.
    """
    if not key or not scores:
        return 0.0
    key = str(key).strip().lower()
    raw = scores.get(key)
    if raw is None:
        return 0.0
    peak = max(abs(v) for v in scores.values()) or 1.0
    return max(-1.0, min(raw / peak, 1.0))


@dataclass
class SongStats:
    """Catalog-wide aggregates for one song, independent of any user."""
    completion_rate: float = 0.0
    skip_rate: float = 0.0
    distinct_listeners: int = 0


def build_ranking_features(
    song: Any,
    state: UserState,
    sources: Iterable[str],
    cf_score: float = 0.0,
    stats: Optional[SongStats] = None,
    max_play_count: int = 1,
    song_vec: Any = None,
) -> List[float]:
    """The (user, song) feature vector, ordered exactly as config.FEATURE_NAMES.

    Returned as a plain list of float so it can be JSON-logged, asserted on in
    tests, and fed to either backend without conversion.
    """
    stats = stats or SongStats()
    source_set = set(sources)
    vec = song_vec if song_vec is not None else song_vector(song)

    content_cos = linalg.cosine(state.taste_vector, vec) if state.taste_vector is not None else 0.0

    song_id = str(getattr(song, "id", "") or "")
    plays = state.play_counts.get(song_id, 0)
    is_repeat = 1.0 if plays > 0 else 0.0

    # Recency of the user's own last play. High for something just heard, which
    # the prior penalises so the feed does not echo the last hour back.
    last_at = state.last_played_at.get(song_id)
    recency = time_decay(last_at, state.as_of, 7.0) if last_at else 0.0

    duration = float(getattr(song, "duration", 0) or 0)
    if state.mean_duration > 0 and duration > 0:
        duration_dev = min(abs(duration - state.mean_duration) / state.mean_duration, 2.0)
    else:
        duration_dev = 0.0

    language = (getattr(song, "language", None) or "").strip().lower()
    genre = (getattr(song, "genre", None) or "").strip().lower()
    artist_name = (getattr(song, "artist_name", None) or "").strip().lower()
    artist_id = str(getattr(song, "artist_id", "") or "")
    album_id = str(getattr(song, "album_id", "") or "")
    is_explicit = bool(getattr(song, "is_explicit", False))
    play_count = int(getattr(song, "play_count", 0) or 0)

    values = {
        "content_cos": content_cos,
        "cf_score": max(0.0, min(cf_score, 1.0)),
        "artist_affinity": _normalized_affinity(state.artist_affinity, artist_name),
        "genre_affinity": _normalized_affinity(state.genre_affinity, genre),
        "language_affinity": _normalized_affinity(state.language_affinity, language),
        "mood_affinity": _normalized_affinity(state.mood_affinity, getattr(song, "mood", None)),
        # log1p ratio keeps a 10k-play hit from dwarfing a 100-play track
        # linearly, which raw counts would.
        "popularity": math.log1p(play_count) / math.log1p(max(max_play_count, 1)),
        "global_completion_rate": stats.completion_rate,
        "global_skip_rate": stats.skip_rate,
        "is_repeat": is_repeat,
        "plays_by_user": min(math.log1p(plays) / math.log1p(20.0), 1.0),
        "recency_decay": recency,
        "lang_pref_match": 1.0 if language and language in state.preferred_languages else 0.0,
        "genre_pref_match": 1.0 if genre and genre in state.favorite_genres else 0.0,
        "artist_pref_match": 1.0 if artist_name and artist_name in state.favorite_artists else 0.0,
        "explicit_penalty": 1.0 if (is_explicit and not state.allows_explicit) else 0.0,
        "duration_dev": duration_dev,
        "artist_followed": 1.0 if artist_id and artist_id in state.followed_artists else 0.0,
        "album_saved": 1.0 if album_id and album_id in state.saved_album_ids else 0.0,
        "in_user_playlist": 1.0 if song_id in state.playlist_song_ids else 0.0,
        "novelty": 0.0 if plays > 0 else 1.0,
        "src_content": 1.0 if "content" in source_set else 0.0,
        "src_cf": 1.0 if "cf" in source_set else 0.0,
        "src_artist_genre": 1.0 if "artist_genre" in source_set else 0.0,
        "src_popular": 1.0 if "popular" in source_set else 0.0,
        "src_playlist": 1.0 if "playlist" in source_set else 0.0,
    }

    return [float(values[name]) for name in config.FEATURE_NAMES]
