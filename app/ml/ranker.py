"""Scores (user, song) pairs with a linear model.

`sigmoid(w.x + b)` where `w` is either the hand-tuned prior from
`config.PRIOR_WEIGHTS` or coefficients learned by `training.py`. Both take the
identical code path -- that is the whole point of the design. A linear model is
also what makes `explain()` possible, and being able to say *why* a song was
recommended is worth more here than the extra accuracy a tree ensemble would buy
on a few hundred interactions.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.ml import config, linalg
from app.ml.features import SongStats, UserState, build_ranking_features, song_vector

logger = logging.getLogger("ml.ranker")


@dataclass
class ScoredSong:
    """A ranked candidate, carrying enough provenance to debug and diversify."""
    song: Any
    score: float
    features: List[float] = field(default_factory=list)
    sources: Set[str] = field(default_factory=set)
    cf_score: float = 0.0
    vector: Any = None

    @property
    def song_id(self) -> str:
        return str(getattr(self.song, "id", "") or "")


class Ranker:
    """A weight vector plus the bookkeeping to apply it consistently."""

    def __init__(
        self,
        weights: Optional[Sequence[float]] = None,
        bias: float = config.PRIOR_BIAS,
        version: Optional[int] = None,
    ):
        self.weights = list(weights) if weights is not None else list(config.PRIOR_WEIGHT_VECTOR)
        self.bias = float(bias)
        self.version = version
        if len(self.weights) != len(config.FEATURE_NAMES):
            raise ValueError(
                f"ranker needs {len(config.FEATURE_NAMES)} weights, got {len(self.weights)}"
            )

    @property
    def is_learned(self) -> bool:
        return self.version is not None

    def score_features(self, features: Sequence[float]) -> float:
        return linalg.sigmoid(linalg.score_linear(features, self.weights, self.bias))

    def rank(
        self,
        songs: Iterable[Any],
        state: UserState,
        sources: Optional[Dict[str, Set[str]]] = None,
        cf_scores: Optional[Dict[str, float]] = None,
        stats: Optional[Dict[str, SongStats]] = None,
        max_play_count: int = 1,
        vectors: Optional[Dict[str, Any]] = None,
    ) -> List[ScoredSong]:
        """Score and sort candidates, best first.

        `sources`, `cf_scores`, `stats` and `vectors` are all keyed by song id and
        all optional -- a caller with no CF model and no play history still gets a
        sensible content-and-popularity ranking.
        """
        sources = sources or {}
        cf_scores = cf_scores or {}
        stats = stats or {}
        vectors = vectors or {}

        out: List[ScoredSong] = []
        for song in songs:
            song_id = str(getattr(song, "id", "") or "")
            vec = vectors.get(song_id)
            if vec is None:
                vec = song_vector(song)
                vectors[song_id] = vec
            song_sources = sources.get(song_id, set())
            cf = float(cf_scores.get(song_id, 0.0))
            features = build_ranking_features(
                song,
                state,
                sources=song_sources,
                cf_score=cf,
                stats=stats.get(song_id),
                max_play_count=max_play_count,
                song_vec=vec,
            )
            out.append(
                ScoredSong(
                    song=song,
                    score=self.score_features(features),
                    features=features,
                    sources=set(song_sources),
                    cf_score=cf,
                    vector=vec,
                )
            )

        out.sort(key=lambda s: s.score, reverse=True)
        return out

    def explain(self, features: Sequence[float], top_n: int = 5) -> List[Tuple[str, float]]:
        """The largest-magnitude weight*feature contributions to a score.

        Feeds the human-readable `reason` on a recommendation shelf and is the
        fastest way to see why a bad recommendation happened.
        """
        contributions = [
            (name, float(f) * float(w))
            for name, f, w in zip(config.FEATURE_NAMES, features, self.weights)
        ]
        contributions.sort(key=lambda p: abs(p[1]), reverse=True)
        return contributions[:top_n]


_PRIOR_RANKER = Ranker()


def prior_ranker() -> Ranker:
    """The untrained baseline. Also the comparison target for promotion."""
    return _PRIOR_RANKER


async def load_ranker(db: AsyncSession) -> Ranker:
    """The active learned ranker, or the prior if none has been promoted."""
    from app.ml import registry

    weights, bias, version = await registry.load_ranker_weights(db)
    try:
        return Ranker(weights=weights, bias=bias, version=version)
    except ValueError as exc:
        logger.warning("rejecting stored ranker (%s); using prior weights", exc)
        return prior_ranker()
