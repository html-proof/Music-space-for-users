"""Train the ranking model. Standalone, so it can run as a Render cron job.

    python scripts/train_ml.py                 # train, promote only if the gate passes
    python scripts/train_ml.py --dry-run       # report metrics, persist nothing
    python scripts/train_ml.py --force         # promote even if the gate fails
    python scripts/train_ml.py --show-weights  # print the fitted coefficients

Running in its own process is the point: a training pass holds the whole feature
matrix in memory and saturates a core, which on a shared 512MB instance is paid
for by request latency. As a cron job it competes with nothing.

Exit codes are meaningful for a scheduler:
  0  a model was trained (whether or not it was promoted)
  1  training could not run (no data, no database, unexpected error)
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.settings import settings  # noqa: E402
from app.db.database import async_session_factory, engine  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("train_ml")


def _fmt(metrics: dict) -> str:
    if not metrics:
        return "n/a"
    if "error" in metrics:
        return str(metrics["error"])
    keys = ("auc", "log_loss", "recall@10", "ndcg@10", "map@10")
    return "  ".join(
        f"{k}={metrics[k]:.4f}" for k in keys if isinstance(metrics.get(k), (int, float))
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Train the GaanaPy ranking model")
    parser.add_argument("--dry-run", action="store_true", help="do not write any artifact")
    parser.add_argument("--force", action="store_true", help="promote even if the quality gate fails")
    parser.add_argument("--no-sklearn", action="store_true", help="use the built-in SGD fitter")
    parser.add_argument("--show-weights", action="store_true", help="print fitted coefficients")
    parser.add_argument("--json", action="store_true", help="emit the summary as JSON only")
    args = parser.parse_args()

    # Imported after sys.path is fixed and argparse has run, so --help does not
    # pay for numpy.
    from app.ml import config, training

    if not args.json:
        print(f"database: {settings.DATABASE_URL.split('@')[-1]}")
        print(f"features: {len(config.FEATURE_NAMES)}")
        print(f"gate:     n_samples >= {config.MIN_TRAINING_SAMPLES}, "
              f"n_users >= {config.MIN_TRAINING_USERS}, AUC >= {config.PROMOTION_MIN_AUC}")
        print()

    try:
        async with async_session_factory() as db:
            result = await training.train(
                db,
                persist=not args.dry_run,
                force_promote=args.force,
                prefer_sklearn=not args.no_sklearn,
            )
    except Exception as e:
        logger.exception("training failed")
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    summary = result.summary()
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return 0 if result.weights else 1

    info = result.info
    print(f"events           : {info.get('n_events', 0)}  "
          f"(songs in catalogue: {info.get('n_songs', 0)}, CF songs: {info.get('cf_songs', 0)})")
    print(f"train rows       : {info.get('n_train', 0)}  "
          f"({info.get('n_train_positive', 0)} positive)")
    print(f"holdout rows     : {info.get('n_holdout', 0)}")
    print(f"users            : {info.get('n_train_users', 0)}")
    print(f"split at         : {info.get('cutoff') or 'no time split (too few events)'}")
    print(f"solver           : {result.solver}")
    print()
    print(f"learned          : {_fmt(result.learned_metrics)}")
    print(f"prior (baseline) : {_fmt(result.prior_metrics)}")
    if result.incumbent_metrics:
        print(f"active model     : {_fmt(result.incumbent_metrics)}")
    print()
    print(f"promoted         : {result.promoted}")
    print(f"reason           : {result.reason}")

    if args.show_weights and result.weights:
        print()
        print("coefficients (frozen source indicators keep their prior values):")
        pairs = sorted(
            zip(config.FEATURE_NAMES, result.weights),
            key=lambda kv: -abs(kv[1]),
        )
        for name, weight in pairs:
            frozen = " (frozen)" if name.startswith("src_") else ""
            print(f"  {name:<22} {weight:+.4f}{frozen}")
        print(f"  {'bias':<22} {result.bias:+.4f}")

    if not result.weights:
        # No model was produced at all -- distinct from "trained but not promoted".
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
