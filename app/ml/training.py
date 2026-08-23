"""Training: label construction, leak-free feature building, SGD, promotion gate.

## The leakage problem, and how this avoids it

Several ranking features are functions of the user's own history with a song --
`plays_by_user`, `is_repeat`, `recency_decay`, and the affinity features. If the
feature vector for "user U played song S at time T" is built from U's *complete*
history, it already contains the play being predicted. The model learns
`plays_by_user > 0 => positive`, holdout AUC reads ~0.99, and the deployed system
recommends songs the user just heard. This is the single most common way to build
a recommender that looks excellent offline and is worthless in production.

So the dataset builder walks each user's interactions in chronological order and,
for every row, builds features from a `StateAccumulator` snapshot taken *before*
absorbing that row. `tests/test_ml.py` asserts this directly rather than trusting
the comment.

The same reasoning applies to CF: the `item_sim` artifact is rebuilt from
training-period baskets only, so `cf_score` on a holdout row cannot have been
computed from that row.

## Why SGD on a linear model

At a few hundred to a few thousand labelled rows, a linear model with 25 features
is near the ceiling of what the data supports; a gradient-boosted ensemble would
mostly memorise. It also keeps `explain()` honest and the artifact 25 floats
wide. `fit_sklearn` is available when scikit-learn is installed, behind the same
signature, and is used for the L-BFGS solver rather than for extra capacity.

Weights are initialised at `config.PRIOR_WEIGHT_VECTOR`, not at zero. With little
data the fit stays near the hand-tuned prior instead of wandering to whatever
noise fits best, which turns the promotion comparison into "did the data teach us
anything beyond the prior" -- the actual question.
"""
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.history import ListeningHistory
from app.models.playlist import Playlist, PlaylistSong
from app.models.song import Song, LikedSong
from app.models.user import UserPreferences
from app.ml import config, item_similarity, metrics, registry
from app.ml.features import (
    Interaction,
    SongStats,
    build_ranking_features,
    song_vector,
    _as_utc,
)
from app.ml.ranker import Ranker, prior_ranker
from app.ml.user_state import StateAccumulator

logger = logging.getLogger("ml.training")

# Deterministic negative sampling. Reproducible runs make "the model got worse"
# a real signal instead of sampling noise.
TRAINING_SEED = 1337


@dataclass
class LabelledEvent:
    """One user action, with the label and weight training will use for it."""
    user_id: str
    song_id: str
    at: datetime
    label: float
    weight: float
    kind: str
    completion: float = 0.0


@dataclass
class Dataset:
    features: List[List[float]] = field(default_factory=list)
    labels: List[float] = field(default_factory=list)
    weights: List[float] = field(default_factory=list)
    group_ids: List[str] = field(default_factory=list)
    timestamps: List[datetime] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.labels)

    @property
    def n_users(self) -> int:
        return len(set(self.group_ids))

    @property
    def n_positive(self) -> int:
        return sum(1 for y in self.labels if y > 0.5)


async def _load_songs(db: AsyncSession) -> Dict[str, Song]:
    rows = (await db.execute(select(Song))).scalars().all()
    return {str(s.id): s for s in rows}


async def collect_events(db: AsyncSession) -> List[LabelledEvent]:
    """Labelled events from implicit feedback, ordered by user then time.

    Positives: likes, playlist adds, and plays that cleared
    `MIN_COMPLETION_PERCENTAGE`. Negatives: skips -- the only *true* negative
    implicit feedback offers, because the user heard the song and rejected it.

    A play that fell short of the completion threshold but was not flagged as a
    skip is deliberately dropped rather than labelled either way: it is genuinely
    ambiguous (a phone call, a commute ending), and forcing a label on it adds
    noise in exactly the region where the model needs precision.
    """
    events: List[LabelledEvent] = []
    min_completion = float(settings.MIN_COMPLETION_PERCENTAGE)

    hist = (
        await db.execute(
            select(ListeningHistory).order_by(
                ListeningHistory.user_id, ListeningHistory.started_at
            )
        )
    ).scalars().all()

    for row in hist:
        at = _as_utc(row.started_at)
        if at is None:
            continue
        completion = float(row.completion_percentage or 0.0)
        listened = float(row.duration_listened or 0.0)

        if row.skipped:
            label, weight, kind = 0.0, 1.5, "skip"
        elif completion >= min_completion or listened >= settings.MIN_LISTEN_SECONDS:
            label = 1.0
            kind = "complete" if completion >= 80.0 else "play"
            weight = config.SIGNAL_WEIGHTS[kind] * (0.5 + completion / 100.0)
        else:
            continue

        events.append(
            LabelledEvent(
                user_id=str(row.user_id), song_id=str(row.song_id), at=at,
                label=label, weight=weight, kind=kind, completion=completion,
            )
        )

    liked = (
        await db.execute(select(LikedSong).order_by(LikedSong.user_id, LikedSong.liked_at))
    ).scalars().all()
    for row in liked:
        at = _as_utc(row.liked_at)
        if at is None:
            continue
        events.append(
            LabelledEvent(
                user_id=str(row.user_id), song_id=str(row.song_id), at=at,
                label=1.0, weight=config.SIGNAL_WEIGHTS["like"], kind="like",
            )
        )

    pl = (
        await db.execute(
            select(PlaylistSong, Playlist.user_id)
            .join(Playlist, PlaylistSong.playlist_id == Playlist.id)
            .order_by(PlaylistSong.added_at)
        )
    ).all()
    for entry, owner_id in pl:
        at = _as_utc(entry.added_at)
        if at is None:
            continue
        events.append(
            LabelledEvent(
                user_id=str(owner_id), song_id=str(entry.song_id), at=at,
                label=1.0, weight=config.SIGNAL_WEIGHTS["playlist_add"], kind="playlist_add",
            )
        )

    events.sort(key=lambda e: (e.user_id, e.at))
    return events


async def _load_prefs_by_user(db: AsyncSession) -> Dict[str, Dict[str, Any]]:
    rows = (await db.execute(select(UserPreferences))).scalars().all()
    return {
        str(p.user_id): {
            "preferred_languages": list(p.preferred_languages or []),
            "favorite_genres": list(p.favorite_genres or []),
            "favorite_artists": list(p.favorite_artists or []),
            "favorite_moods": list(p.favorite_moods or []),
            "allows_explicit": bool(p.explicit_content),
        }
        for p in rows
    }


def _training_cutoff(events: Sequence[LabelledEvent], holdout_fraction: float) -> Optional[datetime]:
    """Timestamp splitting train from holdout.

    Split by *time*, not at random. Recommendation is forecasting: a random split
    lets the model train on a user's Thursday behaviour and be tested on their
    Tuesday, which inflates every metric and answers a question nobody asked.
    """
    if not events:
        return None
    times = sorted(e.at for e in events)
    idx = int(len(times) * (1.0 - holdout_fraction))
    idx = min(max(idx, 1), len(times) - 1) if len(times) > 1 else 0
    return times[idx]


async def build_dataset(
    db: AsyncSession,
    negatives_per_positive: int = config.NEGATIVE_SAMPLES_PER_POSITIVE,
    holdout_fraction: float = config.HOLDOUT_FRACTION,
    seed: int = TRAINING_SEED,
) -> Tuple[Dataset, Dataset, Dict[str, Any]]:
    """(train, holdout, info) with strictly causal features.

    Also returns the training-period CF neighbour map in `info`, so the caller can
    persist the same artifact the features were built from.
    """
    events = await collect_events(db)
    songs = await _load_songs(db)
    prefs_by_user = await _load_prefs_by_user(db)
    cutoff = _training_cutoff(events, holdout_fraction)

    info: Dict[str, Any] = {
        "n_events": len(events),
        "n_songs": len(songs),
        "cutoff": cutoff.isoformat() if cutoff else None,
    }
    if not events or not songs:
        return Dataset(), Dataset(), info

    # CF built from training-period baskets only. Using the full-history artifact
    # would put post-cutoff co-occurrence into a pre-cutoff feature -- the same
    # leak as the per-row state problem, one level up.
    baskets = await item_similarity.collect_baskets(db)
    train_baskets = _restrict_baskets_to_period(baskets, events, cutoff)
    neighbors = item_similarity.build_neighbors(train_baskets)
    info["cf_songs"] = len(neighbors)
    info["cf_neighbor_map"] = neighbors

    global_stats = _global_stats_before(events, cutoff)
    max_play = max((int(s.play_count or 0) for s in songs.values()), default=1) or 1
    vectors = {sid: song_vector(song) for sid, song in songs.items()}
    song_ids = list(songs.keys())

    rng = random.Random(seed)
    train, holdout = Dataset(), Dataset()

    # Group events per user so each user gets one accumulator replayed in order.
    by_user: Dict[str, List[LabelledEvent]] = {}
    for event in events:
        by_user.setdefault(event.user_id, []).append(event)

    for user_id, user_events in by_user.items():
        prefs = prefs_by_user.get(user_id, {})
        acc = StateAccumulator(
            user_id=user_id,
            preferred_languages=prefs.get("preferred_languages"),
            favorite_genres=prefs.get("favorite_genres"),
            favorite_artists=prefs.get("favorite_artists"),
            favorite_moods=prefs.get("favorite_moods"),
            allows_explicit=prefs.get("allows_explicit", True),
        )
        engaged: Set[str] = set()

        for event in user_events:
            song = songs.get(event.song_id)
            if song is None:
                continue

            # ---- The leakage boundary. Everything above this line is state from
            # ---- strictly earlier events; `event` is absorbed only after the
            # ---- features for it have been built.
            state = acc.snapshot(event.at)
            target = holdout if (cutoff and event.at >= cutoff) else train

            cf_scores = item_similarity.score_candidates(
                neighbors, state.recent_song_ids[:20], exclude=set()
            )

            _append_row(
                target, event.user_id, song, state, event.label, event.weight, event.at,
                cf_scores, global_stats, max_play, vectors,
            )

            # Sampled negatives: implicit feedback records no true rejections
            # beyond skips, so without these the model sees almost only positives
            # and learns to output 1.0 for everything.
            if event.label > 0.5 and negatives_per_positive > 0:
                for neg_id in _sample_negatives(
                    rng, song_ids, engaged | {event.song_id}, negatives_per_positive
                ):
                    neg_song = songs.get(neg_id)
                    if neg_song is None:
                        continue
                    _append_row(
                        target, event.user_id, neg_song, state, 0.0, 0.5, event.at,
                        cf_scores, global_stats, max_play, vectors,
                    )

            acc.note_duration(song.duration)
            acc.absorb(
                Interaction(
                    song_id=event.song_id,
                    kind=event.kind,
                    at=event.at,
                    completion=event.completion,
                    artist_name=song.artist_name or "",
                    genre=song.genre,
                    language=song.language,
                    mood=song.mood,
                ),
                song_vec=vectors.get(event.song_id),
            )
            engaged.add(event.song_id)

    info.update({
        "n_train": len(train),
        "n_holdout": len(holdout),
        "n_train_users": train.n_users,
        "n_train_positive": train.n_positive,
    })
    logger.info("dataset built: %s", {k: v for k, v in info.items() if k != "cf_neighbor_map"})
    return train, holdout, info


def _restrict_baskets_to_period(
    baskets: Sequence[Sequence[str]],
    events: Sequence[LabelledEvent],
    cutoff: Optional[datetime],
) -> List[List[str]]:
    """Drop songs whose first engagement is at or after the cutoff.

    `collect_baskets` returns song ids without timestamps, so this filters by
    song rather than by row -- coarser than an exact per-row cut, but it removes
    the post-cutoff items entirely, which is what matters for not leaking them
    into a pre-cutoff similarity.
    """
    if cutoff is None:
        return [list(b) for b in baskets]
    allowed = {e.song_id for e in events if e.at < cutoff}
    out = []
    for basket in baskets:
        filtered = [s for s in basket if s in allowed]
        if len(filtered) > 1:
            out.append(filtered)
    return out


def _global_stats_before(
    events: Sequence[LabelledEvent],
    cutoff: Optional[datetime],
) -> Dict[str, SongStats]:
    """Per-song completion/skip rates computed from pre-cutoff events only.

    `user_state.load_song_stats` aggregates over the whole table, which is right
    for serving and wrong here: a holdout row's `global_skip_rate` would include
    the holdout period.
    """
    plays: Dict[str, int] = {}
    skips: Dict[str, int] = {}
    completion: Dict[str, float] = {}
    listeners: Dict[str, Set[str]] = {}

    for event in events:
        if cutoff and event.at >= cutoff:
            continue
        if event.kind not in ("play", "complete", "skip"):
            continue
        plays[event.song_id] = plays.get(event.song_id, 0) + 1
        completion[event.song_id] = completion.get(event.song_id, 0.0) + event.completion
        listeners.setdefault(event.song_id, set()).add(event.user_id)
        if event.kind == "skip":
            skips[event.song_id] = skips.get(event.song_id, 0) + 1

    return {
        song_id: SongStats(
            completion_rate=min(max((completion.get(song_id, 0.0) / n) / 100.0, 0.0), 1.0),
            skip_rate=min(max(skips.get(song_id, 0) / n, 0.0), 1.0),
            distinct_listeners=len(listeners.get(song_id, ())),
        )
        for song_id, n in plays.items() if n > 0
    }


def _sample_negatives(
    rng: random.Random,
    song_ids: Sequence[str],
    exclude: Set[str],
    n: int,
) -> List[str]:
    """Uniform negatives from the catalog, excluding anything the user engaged with.

    Uniform rather than popularity-weighted on purpose: popularity-weighted
    negatives make the model learn "popular = bad", because popular songs then
    appear disproportionately as negatives.
    """
    if not song_ids:
        return []
    out: List[str] = []
    attempts = 0
    budget = n * 12  # bounded rejection sampling; a tiny catalog can exhaust choices
    while len(out) < n and attempts < budget:
        attempts += 1
        pick = song_ids[rng.randrange(len(song_ids))]
        if pick in exclude or pick in out:
            continue
        out.append(pick)
    return out


def _append_row(
    ds: Dataset,
    user_id: str,
    song: Song,
    state,
    label: float,
    weight: float,
    at: datetime,
    cf_scores: Dict[str, float],
    global_stats: Dict[str, SongStats],
    max_play: int,
    vectors: Dict[str, Any],
) -> None:
    song_id = str(song.id)
    features = build_ranking_features(
        song,
        state,
        # Empty: retrieval provenance is unknowable for a historical row. See
        # config.FROZEN_FEATURE_INDICES for why that is safe rather than lossy.
        sources=(),
        cf_score=cf_scores.get(song_id, 0.0),
        stats=global_stats.get(song_id),
        max_play_count=max_play,
        song_vec=vectors.get(song_id),
    )
    ds.features.append(features)
    ds.labels.append(label)
    ds.weights.append(weight)
    ds.group_ids.append(user_id)
    ds.timestamps.append(at)


def fit_sgd(
    ds: Dataset,
    epochs: int = config.SGD_EPOCHS,
    lr: float = config.SGD_LEARNING_RATE,
    l2: float = config.SGD_L2,
    seed: int = TRAINING_SEED,
    init_weights: Optional[Sequence[float]] = None,
    init_bias: float = config.PRIOR_BIAS,
) -> Tuple[List[float], float]:
    """Weighted logistic regression by SGD. Returns (weights, bias).

    numpy is imported here rather than through `linalg` because training has no
    fallback path: a slow-but-correct pure-Python optimiser would be a second
    implementation to keep in sync for a code path that never runs in production.
    """
    import numpy as np

    n_features = len(config.FEATURE_NAMES)
    w = np.array(
        list(init_weights) if init_weights is not None else config.PRIOR_WEIGHT_VECTOR,
        dtype=np.float64,
    )
    b = float(init_bias)
    if not ds.features:
        return [float(v) for v in w], b

    X = np.asarray(ds.features, dtype=np.float64)
    y = np.asarray(ds.labels, dtype=np.float64)
    sw = np.asarray(ds.weights, dtype=np.float64)
    sw = sw / (sw.mean() or 1.0)

    frozen = np.zeros(n_features, dtype=bool)
    for i in config.FROZEN_FEATURE_INDICES:
        frozen[i] = True
    trainable = ~frozen

    rng = np.random.default_rng(seed)
    n = X.shape[0]
    for epoch in range(epochs):
        order = rng.permutation(n)
        # Decaying step size: large early steps move away from the prior quickly,
        # small late steps stop the last few samples from dominating the fit.
        step = lr / (1.0 + epoch * 0.05)
        for i in order:
            z = float(X[i] @ w) + b
            p = 1.0 / (1.0 + math.exp(-min(max(z, -60.0), 60.0)))
            error = (p - y[i]) * sw[i]
            grad = error * X[i]
            w[trainable] -= step * (grad[trainable] + l2 * w[trainable])
            b -= step * error

    return [float(v) for v in w], float(b)


def has_sklearn() -> bool:
    """Whether the optional accelerator is importable, for /api/ml/status."""
    try:
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def fit_sklearn(ds: Dataset, l2: float = config.SGD_L2) -> Optional[Tuple[List[float], float]]:
    """L-BFGS logistic regression, if scikit-learn is installed.

    Returns None when unavailable so the caller falls back to `fit_sgd`. sklearn
    is an optional accelerator, not a capability upgrade: it lives in
    requirements-ml.txt because ~150MB of scipy on a 512MB instance is a real
    cost for a model this small.

    Frozen coefficients are handled by dropping those columns from the fit and
    splicing the prior values back in, since sklearn cannot pin individual
    coefficients.
    """
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return None
    if not ds.features:
        return None

    X = np.asarray(ds.features, dtype=np.float64)
    y = np.asarray(ds.labels, dtype=np.float64)
    if len(set(ds.labels)) < 2:
        return None

    keep = [i for i in range(len(config.FEATURE_NAMES)) if i not in config.FROZEN_FEATURE_INDICES]
    try:
        model = LogisticRegression(
            C=1.0 / max(l2, 1e-9), max_iter=2000, solver="lbfgs",
        )
        model.fit(X[:, keep], y, sample_weight=np.asarray(ds.weights, dtype=np.float64))
    except Exception as exc:
        logger.warning("sklearn fit failed (%s); falling back to SGD", exc)
        return None

    weights = list(config.PRIOR_WEIGHT_VECTOR)
    for slot, feature_index in enumerate(keep):
        weights[feature_index] = float(model.coef_[0][slot])
    return weights, float(model.intercept_[0])


def score_dataset(ranker: Ranker, ds: Dataset) -> List[float]:
    return [ranker.score_features(f) for f in ds.features]


def evaluate_ranker(ranker: Ranker, ds: Dataset, k: int = 10) -> Dict[str, Any]:
    if not ds.features:
        return {"auc": 0.5, "n_samples": 0}
    return metrics.evaluate(ds.labels, score_dataset(ranker, ds), ds.group_ids, k=k)


@dataclass
class TrainingResult:
    promoted: bool
    reason: str
    weights: List[float] = field(default_factory=list)
    bias: float = 0.0
    learned_metrics: Dict[str, Any] = field(default_factory=dict)
    prior_metrics: Dict[str, Any] = field(default_factory=dict)
    incumbent_metrics: Dict[str, Any] = field(default_factory=dict)
    info: Dict[str, Any] = field(default_factory=dict)
    solver: str = "sgd"

    def summary(self) -> Dict[str, Any]:
        return {
            "promoted": self.promoted,
            "reason": self.reason,
            "solver": self.solver,
            "learned": {k: v for k, v in self.learned_metrics.items()},
            "prior": {k: v for k, v in self.prior_metrics.items()},
            "incumbent": {k: v for k, v in self.incumbent_metrics.items()},
            "dataset": {k: v for k, v in self.info.items() if k != "cf_neighbor_map"},
        }


async def train(
    db: AsyncSession,
    persist: bool = True,
    force_promote: bool = False,
    prefer_sklearn: bool = True,
) -> TrainingResult:
    """Full pass: build dataset, fit, evaluate, gate, persist.

    The gate is the load-bearing part at this data volume. A model that clears
    none of these conditions is still saved (with `is_active=False` and the reason
    in `notes`) so its metrics are inspectable via `GET /api/ml/status` -- it just
    never reaches a user.
    """
    train_ds, holdout_ds, info = await build_dataset(db)
    neighbors = info.pop("cf_neighbor_map", {})

    if len(train_ds) == 0:
        return TrainingResult(
            promoted=False,
            reason="no labelled interactions found; serving prior weights",
            info=info,
        )

    solver = "sgd"
    fitted = fit_sklearn(train_ds) if prefer_sklearn else None
    if fitted is not None:
        solver = "sklearn"
        weights, bias = fitted
    else:
        weights, bias = fit_sgd(train_ds)

    learned = Ranker(weights=weights, bias=bias, version=-1)
    prior = prior_ranker()
    # With no holdout (too few rows to split) metrics are reported on train and
    # the sample gate below refuses promotion anyway -- so this cannot promote a
    # model evaluated on its own training data.
    eval_ds = holdout_ds if len(holdout_ds) > 0 else train_ds

    learned_metrics = evaluate_ranker(learned, eval_ds)
    prior_metrics = evaluate_ranker(prior, eval_ds)
    learned_metrics["evaluated_on"] = "holdout" if len(holdout_ds) > 0 else "train"

    incumbent_metrics: Dict[str, Any] = {}
    incumbent_auc = 0.0
    entry = await registry.load_active(db, "ranker", use_cache=False)
    if entry:
        try:
            incumbent = Ranker(
                weights=(entry["artifact"] or {}).get("weights"),
                bias=float((entry["artifact"] or {}).get("bias", 0.0)),
                version=entry["version"],
            )
            incumbent_metrics = evaluate_ranker(incumbent, eval_ds)
            incumbent_auc = float(incumbent_metrics.get("auc", 0.0))
        except (ValueError, TypeError):
            # An incumbent fitted on a different feature set cannot be compared;
            # treat it as absent rather than blocking promotion forever.
            incumbent_metrics = {"error": "incompatible feature set"}

    auc = float(learned_metrics.get("auc", 0.0))
    prior_auc = float(prior_metrics.get("auc", 0.0))
    reasons: List[str] = []
    if len(train_ds) < config.MIN_TRAINING_SAMPLES:
        reasons.append(
            f"n_samples {len(train_ds)} < ML_MIN_TRAINING_SAMPLES {config.MIN_TRAINING_SAMPLES}"
        )
    if train_ds.n_users < config.MIN_TRAINING_USERS:
        reasons.append(f"n_users {train_ds.n_users} < {config.MIN_TRAINING_USERS}")
    if auc < config.PROMOTION_MIN_AUC:
        reasons.append(f"holdout AUC {auc:.4f} < {config.PROMOTION_MIN_AUC}")
    if auc <= prior_auc:
        reasons.append(f"holdout AUC {auc:.4f} does not beat prior {prior_auc:.4f}")
    if incumbent_auc and auc <= incumbent_auc:
        reasons.append(f"holdout AUC {auc:.4f} does not beat active model {incumbent_auc:.4f}")

    promoted = not reasons
    if force_promote and reasons:
        reasons.append("force_promote overrode the gate")
        promoted = True

    reason = "promoted" if promoted and not force_promote else (
        "; ".join(reasons) if reasons else "promoted"
    )

    result = TrainingResult(
        promoted=promoted, reason=reason, weights=weights, bias=bias,
        learned_metrics=learned_metrics, prior_metrics=prior_metrics,
        incumbent_metrics=incumbent_metrics, info=info, solver=solver,
    )

    if persist:
        await registry.save_artifact(
            db, "ranker",
            artifact={
                "weights": weights,
                "bias": bias,
                "feature_names": list(config.FEATURE_NAMES),
                "solver": solver,
            },
            metrics={"learned": learned_metrics, "prior": prior_metrics},
            n_samples=len(train_ds), n_users=train_ds.n_users,
            activate=promoted, notes=reason,
        )
        if neighbors:
            # item_sim ships regardless of the ranker gate: co-occurrence
            # counting has no fitted parameters to overfit, and shrinkage already
            # suppresses thin pairs.
            full_neighbors, cf_stats = await item_similarity.build_from_db(db)
            if full_neighbors:
                await registry.save_artifact(
                    db, "item_sim",
                    artifact={"neighbors": {k: [[i, round(s, 6)] for i, s in v]
                                            for k, v in full_neighbors.items()}},
                    metrics=cf_stats,
                    n_samples=cf_stats.get("n_baskets", 0),
                    activate=True,
                    notes="co-occurrence CF; no fitted parameters, promoted unconditionally",
                )
        await db.commit()

    logger.info("training finished: %s", result.summary())
    return result
