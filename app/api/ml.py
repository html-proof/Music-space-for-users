"""Model introspection and manual retraining.

`GET /api/ml/status` is deliberately readable by any authenticated user: it
returns which ranker is serving and its offline metrics, which is diagnostic
rather than sensitive, and having it behind the same auth as everything else
makes it usable from a client while debugging a bad feed.

`POST /api/ml/retrain` is guarded by the `ML_ADMIN_TOKEN` shared secret instead.
There is no admin role in `firebase_auth.py` to hang authorization off, and a
training pass is expensive enough that an unauthenticated one is a free denial
of service. With no token configured the endpoint refuses every request rather
than defaulting to open.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import get_db
from app.middleware.firebase_auth import get_current_user
from app.ml import config, registry, training
from app.models.user import User
from app.utils.response import api_error, api_response

logger = logging.getLogger("ml_api")

router = APIRouter(prefix="/api/ml", tags=["Machine Learning"])

# One training pass at a time per process. Two concurrent passes would both
# read the same events, fit the same model and race on which version ends up
# active -- and on a small instance they would also compete for the memory the
# feature matrix needs.
_train_lock = asyncio.Lock()


def _authorize(token: Optional[str]) -> Optional[str]:
    """Return an error code, or None when the request may proceed."""
    configured = (settings.ML_ADMIN_TOKEN or "").strip()
    if not configured:
        return "not_configured"
    supplied = (token or "").strip()
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    # Length-independent comparison: `==` on str short-circuits at the first
    # differing byte, which leaks the shared secret one character at a time to
    # anyone who can measure the response.
    if len(supplied) != len(configured):
        return "forbidden"
    if sum(a != b for a, b in zip(supplied, configured)):
        return "forbidden"
    return None


@router.get("", summary="ML subsystem status")
@router.get("/status", summary="ML subsystem status")
async def ml_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Which model is serving, its metrics, and the active hyperparameters."""
    try:
        state = await registry.status(db)
    except Exception as e:
        # A missing `ml_models` table must not turn the diagnostic endpoint into
        # a 500 -- reporting "nothing trained yet" is the useful answer.
        logger.warning("ml status unavailable: %s", e)
        state = {
            "feature_count": len(config.FEATURE_NAMES),
            "feature_names": list(config.FEATURE_NAMES),
            "models": {},
            "ranker_in_use": {"source": "prior", "version": None},
            "error": "model registry unavailable",
        }

    from app.ml import linalg

    state["settings"] = {
        "ml_enabled": settings.ML_ENABLED,
        "auto_train_enabled": settings.ML_AUTO_TRAIN_ENABLED,
        "train_interval_minutes": settings.ML_TRAIN_INTERVAL_MINUTES,
        "min_training_samples": settings.ML_MIN_TRAINING_SAMPLES,
        "exploration_rate": settings.ML_EXPLORATION_RATE,
        "embedding_dim": settings.ML_EMBEDDING_DIM,
        "candidate_limit": settings.ML_CANDIDATE_LIMIT,
        "retrain_endpoint_configured": bool((settings.ML_ADMIN_TOKEN or "").strip()),
    }
    state["backend"] = {
        "numpy": linalg.HAS_NUMPY,
        "sklearn": training.has_sklearn(),
    }
    return api_response(state)


@router.post("/retrain", summary="Retrain the ranker (admin token required)")
async def retrain(
    x_ml_admin_token: Optional[str] = Header(default=None, alias="X-ML-Admin-Token"),
    force: bool = Query(False, description="Promote even if the quality gate fails"),
    dry_run: bool = Query(False, description="Train and report metrics without persisting"),
    db: AsyncSession = Depends(get_db),
):
    denial = _authorize(x_ml_admin_token)
    if denial == "not_configured":
        return api_error(
            "ml_retrain_disabled",
            "ML_ADMIN_TOKEN is not set, so retraining over HTTP is disabled. "
            "Run scripts/train_ml.py instead.",
            status_code=503,
        )
    if denial:
        return api_error("forbidden", "Invalid ML admin token", status_code=403)

    if _train_lock.locked():
        return api_error(
            "training_in_progress",
            "A training pass is already running; retry once it finishes.",
            status_code=409,
        )

    async with _train_lock:
        try:
            result = await training.train(
                db, persist=not dry_run, force_promote=force
            )
        except Exception as e:
            logger.exception("retrain failed")
            return api_error("training_failed", f"{type(e).__name__}: {e}", status_code=500)

    summary = result.summary()
    summary["dry_run"] = dry_run
    return api_response(summary)


@router.post("/invalidate-cache", summary="Drop the in-process model cache")
async def invalidate_cache(
    x_ml_admin_token: Optional[str] = Header(default=None, alias="X-ML-Admin-Token"),
):
    """Force every worker to re-read the active artifact on its next request.

    Needed after training from the cron job: that runs in a separate process, so
    the web instances keep serving the previous artifact until their 60-second
    registry cache expires. This makes the switch immediate.
    """
    denial = _authorize(x_ml_admin_token)
    if denial == "not_configured":
        return api_error("ml_retrain_disabled", "ML_ADMIN_TOKEN is not set", status_code=503)
    if denial:
        return api_error("forbidden", "Invalid ML admin token", status_code=403)

    registry.invalidate()
    return api_response({"message": "model cache cleared"})
