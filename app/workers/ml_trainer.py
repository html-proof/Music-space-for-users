"""Background retraining inside the web process.

Off by default (`ML_AUTO_TRAIN_ENABLED=False`). On a single small instance a
training pass competes with request handling for both CPU and memory, so the
Render cron job running `scripts/train_ml.py` in its own process is the better
default. This loop exists for deployments with no cron facility.

Two properties matter more than the schedule itself:

- **It never trains on nothing new.** Retraining every six hours on an unchanged
  interaction table burns CPU to produce a byte-identical artifact, and each pass
  writes a new `ml_models` row. The loop counts interactions and skips the pass
  when too few have arrived.
- **It never takes the process down.** Every failure is logged and the loop
  continues; an exception escaping here would kill the task silently and leave
  the deployment believing it still retrains.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from app.config.settings import settings
from app.db.database import async_session_factory
from app.models.history import ListeningHistory
from app.models.song import LikedSong

logger = logging.getLogger("ml_trainer")

# Wait before the first pass so startup -- migrations, Firebase init, the first
# burst of requests -- is not competing with a training run.
INITIAL_DELAY_SECONDS = 120
# New labelled interactions required before a pass is worth running.
MIN_NEW_INTERACTIONS = 25
# Backoff after a failure, so a persistent error (missing table, unreachable DB)
# does not spin.
ERROR_BACKOFF_SECONDS = 900


async def _interaction_count() -> Optional[int]:
    """Rows that could become training labels. None if the count failed."""
    try:
        async with async_session_factory() as db:
            history = (await db.execute(select(func.count()).select_from(ListeningHistory))).scalar_one()
            likes = (await db.execute(select(func.count()).select_from(LikedSong))).scalar_one()
            return int(history or 0) + int(likes or 0)
    except Exception as e:
        logger.warning("could not count interactions: %s", e)
        return None


async def _train_once() -> None:
    # Imported here, not at module scope: this pulls in numpy and the whole ml
    # package, which should not be paid for when auto-training is disabled.
    from app.ml import training

    async with async_session_factory() as db:
        result = await training.train(db, persist=True)
    logger.info(
        "auto-train pass finished: promoted=%s reason=%s",
        result.promoted, result.reason,
    )


async def auto_train_loop() -> None:
    """Retrain on an interval, skipping passes with too little new data."""
    interval = max(int(settings.ML_TRAIN_INTERVAL_MINUTES), 5) * 60
    logger.info(
        "ML auto-train loop started: every %d minutes, >=%d new interactions per pass",
        interval // 60, MIN_NEW_INTERACTIONS,
    )
    await asyncio.sleep(INITIAL_DELAY_SECONDS)

    last_count: Optional[int] = None
    while True:
        try:
            count = await _interaction_count()
            if count is None:
                await asyncio.sleep(ERROR_BACKOFF_SECONDS)
                continue

            if last_count is not None and count - last_count < MIN_NEW_INTERACTIONS:
                logger.info(
                    "skipping auto-train: %d new interactions since last pass (<%d)",
                    count - last_count, MIN_NEW_INTERACTIONS,
                )
            else:
                started = datetime.now(timezone.utc)
                await _train_once()
                last_count = count
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                logger.info("auto-train took %.1fs on %d interactions", elapsed, count)

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("ML auto-train loop cancelled")
            raise
        except Exception:
            logger.exception("auto-train pass failed; backing off")
            await asyncio.sleep(ERROR_BACKOFF_SECONDS)
