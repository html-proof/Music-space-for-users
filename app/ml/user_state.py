"""Builds `UserState` snapshots, for serving and for training.

Two builders, one update rule:

- `build_user_state` runs a handful of queries and is used on the request path.
- `StateAccumulator` folds interactions in one at a time, in chronological
  order. Training needs a state snapshot *as of* every labelled interaction; doing
  that with `build_user_state` would be one query set per row (O(n) round trips),
  and re-aggregating from scratch per row would be O(n^2) work. The accumulator
  makes it a single pass.

Both funnel every mutation through `StateAccumulator.absorb`, so a feature
computed during training cannot drift from the same feature computed while
serving.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlalchemy import Integer, select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import is_uuid
from app.models.history import ListeningHistory
from app.models.playlist import Playlist, PlaylistSong
from app.models.song import Song, LikedSong, SavedAlbum, FollowedArtist
from app.models.user import UserPreferences
from app.ml import config, linalg
from app.ml.features import (
    Interaction,
    SongStats,
    UserState,
    preference_vector,
    song_vector,
    _as_utc,
)

logger = logging.getLogger("ml.user_state")

# How much history to aggregate on the serving path. Taste decays with a 60-day
# half-life anyway, so older rows contribute almost nothing while still costing
# a row fetch.
SERVING_HISTORY_LIMIT = 500


class StateAccumulator:
    """Mutable user state that absorbs interactions in chronological order."""

    def __init__(
        self,
        user_id: str,
        dim: int = config.EMBEDDING_DIM,
        preferred_languages: Optional[Sequence[str]] = None,
        favorite_genres: Optional[Sequence[str]] = None,
        favorite_artists: Optional[Sequence[str]] = None,
        favorite_moods: Optional[Sequence[str]] = None,
        allows_explicit: bool = True,
    ):
        self.user_id = user_id
        self.dim = dim
        # Unnormalised running sum; normalised only when a snapshot is taken, so
        # each absorbed interaction stays O(dim) instead of forcing a re-norm.
        self._taste_accum = linalg.zeros(dim)
        self._prior_vector = preference_vector(
            preferred_languages or (), favorite_genres or (),
            favorite_artists or (), favorite_moods or (), dim=dim,
        )
        self.artist_affinity: Dict[str, float] = {}
        self.genre_affinity: Dict[str, float] = {}
        self.language_affinity: Dict[str, float] = {}
        self.mood_affinity: Dict[str, float] = {}
        self.play_counts: Dict[str, int] = {}
        self.last_played_at: Dict[str, datetime] = {}
        self.liked_song_ids: Set[str] = set()
        self.playlist_song_ids: Set[str] = set()
        self.followed_artists: Set[str] = set()
        self.saved_album_ids: Set[str] = set()
        self.preferred_languages = {str(v).strip().lower() for v in (preferred_languages or ()) if v}
        self.favorite_genres = {str(v).strip().lower() for v in (favorite_genres or ()) if v}
        self.favorite_artists = {str(v).strip().lower() for v in (favorite_artists or ()) if v}
        self.allows_explicit = allows_explicit
        self._duration_sum = 0.0
        self._duration_n = 0
        self._recent: List[str] = []
        self.n_interactions = 0

    def absorb(self, interaction: Interaction, song_vec: Any = None) -> None:
        """Fold one interaction in. Callers must supply these in time order."""
        weight = interaction.weight
        self.n_interactions += 1

        if song_vec is not None:
            linalg.add_scaled(self._taste_accum, song_vec, weight)

        for scores, key in (
            (self.artist_affinity, interaction.artist_name),
            (self.genre_affinity, interaction.genre),
            (self.language_affinity, interaction.language),
            (self.mood_affinity, interaction.mood),
        ):
            if key:
                k = str(key).strip().lower()
                if k:
                    scores[k] = scores.get(k, 0.0) + weight

        if interaction.kind in ("play", "complete", "skip"):
            self.play_counts[interaction.song_id] = self.play_counts.get(interaction.song_id, 0) + 1
            at = _as_utc(interaction.at)
            if at is not None:
                prev = self.last_played_at.get(interaction.song_id)
                if prev is None or at > prev:
                    self.last_played_at[interaction.song_id] = at
            if interaction.song_id in self._recent:
                self._recent.remove(interaction.song_id)
            self._recent.insert(0, interaction.song_id)
            del self._recent[40:]
        elif interaction.kind == "like":
            self.liked_song_ids.add(interaction.song_id)
        elif interaction.kind == "playlist_add":
            self.playlist_song_ids.add(interaction.song_id)

    def note_duration(self, duration: float) -> None:
        if duration and duration > 0:
            self._duration_sum += float(duration)
            self._duration_n += 1

    def snapshot(self, as_of: datetime) -> UserState:
        """Immutable view for feature extraction at `as_of`.

        The taste vector falls back to declared preferences while there is no
        engagement history, so `content_cos` is informative from the first
        request rather than a constant zero.
        """
        taste = linalg.normalize(self._taste_accum)
        if linalg.l2_norm(taste) <= 1e-12:
            taste = self._prior_vector

        return UserState(
            user_id=self.user_id,
            as_of=as_of,
            taste_vector=taste,
            artist_affinity=dict(self.artist_affinity),
            genre_affinity=dict(self.genre_affinity),
            language_affinity=dict(self.language_affinity),
            mood_affinity=dict(self.mood_affinity),
            play_counts=dict(self.play_counts),
            last_played_at=dict(self.last_played_at),
            liked_song_ids=set(self.liked_song_ids),
            playlist_song_ids=set(self.playlist_song_ids),
            followed_artists=set(self.followed_artists),
            saved_album_ids=set(self.saved_album_ids),
            preferred_languages=set(self.preferred_languages),
            favorite_genres=set(self.favorite_genres),
            favorite_artists=set(self.favorite_artists),
            allows_explicit=self.allows_explicit,
            mean_duration=(self._duration_sum / self._duration_n) if self._duration_n else 0.0,
            recent_song_ids=list(self._recent),
            n_interactions=self.n_interactions,
        )


async def _load_preferences(db: AsyncSession, user_id: str) -> Dict[str, Any]:
    if not is_uuid(user_id):
        return {}
    res = await db.execute(select(UserPreferences).where(UserPreferences.user_id == user_id))
    prefs = res.scalar_one_or_none()
    if not prefs:
        return {}
    return {
        "preferred_languages": list(prefs.preferred_languages or []),
        "favorite_genres": list(prefs.favorite_genres or []),
        "favorite_artists": list(prefs.favorite_artists or []),
        "favorite_moods": list(prefs.favorite_moods or []),
        "allows_explicit": bool(prefs.explicit_content),
    }


async def build_user_state(
    db: AsyncSession,
    user_id: str,
    as_of: Optional[datetime] = None,
    history_limit: int = SERVING_HISTORY_LIMIT,
) -> UserState:
    """Aggregate a user's engagement into a state snapshot.

    `as_of` bounds which rows are considered, so this can also reproduce a
    historical state; it defaults to now for the serving path.
    """
    now = as_of or datetime.now(timezone.utc)
    prefs = await _load_preferences(db, user_id)

    acc = StateAccumulator(
        user_id=user_id,
        preferred_languages=prefs.get("preferred_languages"),
        favorite_genres=prefs.get("favorite_genres"),
        favorite_artists=prefs.get("favorite_artists"),
        favorite_moods=prefs.get("favorite_moods"),
        allows_explicit=prefs.get("allows_explicit", True),
    )

    if not is_uuid(user_id):
        return acc.snapshot(now)

    # Newest first for the LIMIT to keep the most relevant rows, then replayed
    # oldest-first because the accumulator's recency bookkeeping assumes order.
    hist_stmt = (
        select(ListeningHistory, Song)
        .join(Song, ListeningHistory.song_id == Song.id)
        .where(ListeningHistory.user_id == user_id, ListeningHistory.started_at <= now)
        .order_by(desc(ListeningHistory.started_at))
        .limit(history_limit)
    )
    rows = list((await db.execute(hist_stmt)).all())
    rows.reverse()

    vec_cache: Dict[str, Any] = {}

    def _vec(song: Song):
        key = str(song.id)
        if key not in vec_cache:
            vec_cache[key] = song_vector(song)
        return vec_cache[key]

    for hist, song in rows:
        kind = "skip" if hist.skipped else ("complete" if hist.completion_percentage >= 80.0 else "play")
        acc.note_duration(song.duration)
        acc.absorb(
            Interaction(
                song_id=str(song.id),
                kind=kind,
                at=hist.started_at,
                completion=float(hist.completion_percentage or 0.0),
                artist_name=song.artist_name or "",
                genre=song.genre,
                language=song.language,
                mood=song.mood,
            ),
            song_vec=_vec(song),
        )

    liked_stmt = (
        select(LikedSong, Song)
        .join(Song, LikedSong.song_id == Song.id)
        .where(LikedSong.user_id == user_id, LikedSong.liked_at <= now)
        .order_by(LikedSong.liked_at)
    )
    for liked, song in (await db.execute(liked_stmt)).all():
        acc.note_duration(song.duration)
        acc.absorb(
            Interaction(
                song_id=str(song.id), kind="like", at=liked.liked_at,
                artist_name=song.artist_name or "", genre=song.genre,
                language=song.language, mood=song.mood,
            ),
            song_vec=_vec(song),
        )

    # Songs the user curated into their own playlists -- strong intent, and the
    # join through Playlist is what restricts this to playlists they own.
    pl_stmt = (
        select(PlaylistSong, Song)
        .join(Playlist, PlaylistSong.playlist_id == Playlist.id)
        .join(Song, PlaylistSong.song_id == Song.id)
        .where(Playlist.user_id == user_id, PlaylistSong.added_at <= now)
        .order_by(PlaylistSong.added_at)
    )
    for entry, song in (await db.execute(pl_stmt)).all():
        acc.absorb(
            Interaction(
                song_id=str(song.id), kind="playlist_add", at=entry.added_at,
                artist_name=song.artist_name or "", genre=song.genre,
                language=song.language, mood=song.mood,
            ),
            song_vec=_vec(song),
        )

    followed = await db.execute(
        select(FollowedArtist.artist_id).where(FollowedArtist.user_id == user_id)
    )
    acc.followed_artists = {str(a) for a in followed.scalars().all()}

    saved = await db.execute(
        select(SavedAlbum.album_id).where(SavedAlbum.user_id == user_id)
    )
    acc.saved_album_ids = {str(a) for a in saved.scalars().all()}

    return acc.snapshot(now)


async def load_song_stats(
    db: AsyncSession,
    song_ids: Optional[Sequence[str]] = None,
) -> Dict[str, SongStats]:
    """Catalog-wide completion/skip rates per song, from listening history.

    These are the "do people actually finish this track" features. A song nobody
    has played yet gets a neutral SongStats() from the caller's .get() default
    rather than a fabricated rate.
    """
    stmt = (
        select(
            ListeningHistory.song_id,
            func.count().label("plays"),
            func.avg(ListeningHistory.completion_percentage).label("avg_completion"),
            func.sum(func.cast(ListeningHistory.skipped, Integer)).label("skips"),
            func.count(func.distinct(ListeningHistory.user_id)).label("listeners"),
        )
        .group_by(ListeningHistory.song_id)
    )
    if song_ids:
        valid = [s for s in song_ids if is_uuid(s)]
        if not valid:
            return {}
        stmt = stmt.where(ListeningHistory.song_id.in_(valid))

    out: Dict[str, SongStats] = {}
    for row in (await db.execute(stmt)).all():
        plays = int(row.plays or 0)
        if plays <= 0:
            continue
        out[str(row.song_id)] = SongStats(
            completion_rate=min(max(float(row.avg_completion or 0.0) / 100.0, 0.0), 1.0),
            skip_rate=min(max(float(row.skips or 0) / plays, 0.0), 1.0),
            distinct_listeners=int(row.listeners or 0),
        )
    return out


async def max_play_count(db: AsyncSession) -> int:
    """Catalog maximum, used to scale the popularity feature into [0, 1]."""
    res = await db.execute(select(func.max(Song.play_count)))
    return int(res.scalar() or 1) or 1
