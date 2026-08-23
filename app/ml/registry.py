"""Load, cache and promote model artifacts stored in the `ml_models` table.

Three artifact kinds, all keyed by `name`:
  - "ranker"     -> {"weights": [...], "bias": float, "feature_names": [...]}
  - "item_sim"   -> {"neighbors": {song_id: [[other_id, score], ...]}}
  - "popularity" -> {"stats": {song_id: [completion_rate, skip_rate, listeners]}}

Loads are cached in-process with a short TTL. The alternative -- a query per
recommendation request -- would add a round trip to every home-feed render for
data that changes at most once per training interval. The TTL is what lets an
instance pick up a model promoted by a *different* instance without a restart,
which matters because Render runs more than one.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml import config
from app.models.ml import MLModel

logger = logging.getLogger("ml.registry")

# Long enough to absorb a burst of requests, short enough that a promotion on
# another instance is picked up within a minute.
CACHE_TTL_SECONDS = 60

_cache: Dict[str, Tuple[float, Optional[Dict[str, Any]]]] = {}


def invalidate(name: Optional[str] = None) -> None:
    """Drop cached artifacts. Called after a promotion in this process."""
    if name is None:
        _cache.clear()
    else:
        _cache.pop(name, None)


async def load_active(db: AsyncSession, name: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """The active artifact for `name`, or None if nothing has been promoted.

    A None result is a normal state, not an error: it means the caller should
    fall back to prior weights / on-the-fly computation.
    """
    now = time.monotonic()
    if use_cache:
        hit = _cache.get(name)
        if hit and (now - hit[0]) < CACHE_TTL_SECONDS:
            return hit[1]

    try:
        res = await db.execute(
            select(MLModel)
            .where(MLModel.name == name, MLModel.is_active.is_(True))
            .order_by(MLModel.version.desc())
            .limit(1)
        )
        row = res.scalar_one_or_none()
    except Exception as exc:
        # A missing ml_models table (migration 003 not yet applied) must not take
        # down the feed -- the caller's fallback is a correct, if unlearned,
        # ranking. Cached as None so we do not retry on every request.
        logger.warning("ml_models lookup failed for %r (%s); serving fallback", name, exc)
        _cache[name] = (now, None)
        return None

    payload: Optional[Dict[str, Any]] = None
    if row is not None:
        payload = {
            "id": str(row.id),
            "version": row.version,
            "artifact": row.artifact or {},
            "metrics": row.metrics or {},
            "n_samples": row.n_samples,
            "n_users": row.n_users,
            "trained_at": row.trained_at,
            "notes": row.notes,
        }

    _cache[name] = (now, payload)
    return payload


async def load_ranker_weights(db: AsyncSession) -> Tuple[List[float], float, Optional[int]]:
    """(weights, bias, version) for scoring -- learned if valid, else the prior.

    A stored model is rejected unless its recorded `feature_names` match
    `config.FEATURE_NAMES` exactly. Adding or reordering a feature changes the
    meaning of every coefficient position, so scoring an old weight vector
    against a new feature vector would silently produce nonsense; the version
    check turns that into a logged fallback instead.
    """
    entry = await load_active(db, "ranker")
    if not entry:
        return list(config.PRIOR_WEIGHT_VECTOR), config.PRIOR_BIAS, None

    artifact = entry.get("artifact") or {}
    weights = artifact.get("weights")
    names = artifact.get("feature_names")

    if not isinstance(weights, list) or len(weights) != len(config.FEATURE_NAMES):
        logger.warning(
            "ranker v%s has %s weights, expected %d; using prior weights",
            entry.get("version"),
            len(weights) if isinstance(weights, list) else type(weights).__name__,
            len(config.FEATURE_NAMES),
        )
        return list(config.PRIOR_WEIGHT_VECTOR), config.PRIOR_BIAS, None

    if names is not None and list(names) != list(config.FEATURE_NAMES):
        logger.warning(
            "ranker v%s was fitted on a different feature set; using prior weights. "
            "Retrain to pick up the new features.",
            entry.get("version"),
        )
        return list(config.PRIOR_WEIGHT_VECTOR), config.PRIOR_BIAS, None

    try:
        vector = [float(w) for w in weights]
    except (TypeError, ValueError):
        logger.warning("ranker v%s has non-numeric weights; using prior", entry.get("version"))
        return list(config.PRIOR_WEIGHT_VECTOR), config.PRIOR_BIAS, None

    return vector, float(artifact.get("bias", 0.0)), entry.get("version")


async def load_item_neighbors(db: AsyncSession) -> Dict[str, List[Tuple[str, float]]]:
    """song_id -> [(neighbor_id, similarity), ...] from the active item_sim artifact."""
    entry = await load_active(db, "item_sim")
    if not entry:
        return {}
    raw = (entry.get("artifact") or {}).get("neighbors") or {}
    out: Dict[str, List[Tuple[str, float]]] = {}
    for song_id, pairs in raw.items():
        try:
            out[str(song_id)] = [(str(p[0]), float(p[1])) for p in pairs]
        except (TypeError, ValueError, IndexError):
            continue
    return out


async def next_version(db: AsyncSession, name: str) -> int:
    res = await db.execute(
        select(MLModel.version).where(MLModel.name == name).order_by(MLModel.version.desc()).limit(1)
    )
    current = res.scalar_one_or_none()
    return int(current or 0) + 1


async def save_artifact(
    db: AsyncSession,
    name: str,
    artifact: Dict[str, Any],
    metrics: Optional[Dict[str, Any]] = None,
    n_samples: int = 0,
    n_users: int = 0,
    activate: bool = False,
    notes: Optional[str] = None,
) -> MLModel:
    """Persist a new version, optionally promoting it to active.

    Deactivating the previous rows rather than deleting them keeps a rollback
    target and a readable metrics history. Caller commits.
    """
    version = await next_version(db, name)

    if activate:
        await db.execute(
            update(MLModel)
            .where(MLModel.name == name, MLModel.is_active.is_(True))
            .values(is_active=False)
        )

    row = MLModel(
        name=name,
        version=version,
        artifact=artifact,
        metrics=metrics or {},
        n_samples=n_samples,
        n_users=n_users,
        is_active=activate,
        notes=(notes or "")[:1024] or None,
    )
    db.add(row)
    await db.flush()

    if activate:
        invalidate(name)

    logger.info(
        "saved ml artifact name=%s version=%d active=%s n_samples=%d",
        name, version, activate, n_samples,
    )
    return row


async def status(db: AsyncSession) -> Dict[str, Any]:
    """Summary for `GET /api/ml/status`."""
    out: Dict[str, Any] = {
        "feature_count": len(config.FEATURE_NAMES),
        "feature_names": list(config.FEATURE_NAMES),
        "models": {},
    }
    for name in ("ranker", "item_sim", "popularity"):
        entry = await load_active(db, name, use_cache=False)
        if not entry:
            out["models"][name] = {"active": False, "serving": "prior" if name == "ranker" else "computed"}
            continue
        artifact = entry.get("artifact") or {}
        out["models"][name] = {
            "active": True,
            "version": entry["version"],
            "metrics": entry["metrics"],
            "n_samples": entry["n_samples"],
            "n_users": entry["n_users"],
            "trained_at": entry["trained_at"].isoformat() if entry.get("trained_at") else None,
            "notes": entry.get("notes"),
            "serving": "learned",
            "size": len(artifact.get("neighbors") or artifact.get("stats") or artifact.get("weights") or []),
        }

    weights, bias, version = await load_ranker_weights(db)
    out["ranker_in_use"] = {
        "source": "learned" if version is not None else "prior",
        "version": version,
        "bias": bias,
        "weights": dict(zip(config.FEATURE_NAMES, weights)),
    }
    return out
