"""Check followed artists for new songs and notify their followers. Standalone,
so it can run as a Render cron job.

    python scripts/check_new_releases.py

Running in its own process mirrors scripts/train_ml.py: this makes one Gaana
request per followed artist plus a DB write per newly discovered song, which
on a shared small instance is better kept off the request-handling process.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.database import engine  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("check_new_releases")


async def main() -> int:
    from app.workers.release_watch_worker import run_once

    try:
        summary = await run_once()
    except Exception as e:
        logger.exception("release check failed")
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
