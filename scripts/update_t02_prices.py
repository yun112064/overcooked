"""Refresh local 1102_V3 prices from the active SQL recipe release."""

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, replace
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
DATABASE = "meal_planner_1102_v3"
PRICE_ENV = (
    "PRICE_SCHEDULER_ENABLED", "PRICE_SCHEDULER_HOUR", "PRICE_SCHEDULER_MINUTE",
    "PRICE_SCHEDULER_TIMEZONE", "PRICE_COLLECTION_DELAY_SECONDS",
    "PRICE_COLLECTION_TIMEOUT_SECONDS", "PRICE_COLLECTION_RETRIES",
    "PRICE_FALLBACK_UNI_PROSPERITY_ENABLED",
)


def preflight(engine, expected_release):
    from sqlalchemy import text

    url = engine.url
    if (url.get_backend_name(), url.host, url.port, url.database) != (
        "postgresql", "127.0.0.1", 55435, DATABASE,
    ):
        raise SystemExit(f"T02 update requires 127.0.0.1:55435/{DATABASE}")
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        if connection.scalar(text("SELECT current_database()")) != DATABASE:
            raise SystemExit("Connected database does not match the dedicated V3 database")
        release = connection.execute(text(
            "SELECT release_id FROM recipe_data_releases WHERE active"
        )).scalar_one_or_none()
        if not release or release != expected_release:
            raise SystemExit("Active T01 release must match GENERATION_RECIPE_RELEASE_ID")
        for table in ("price_collection_runs", "price_quote_snapshots", "price_snapshot_foods"):
            connection.execute(text(f"SELECT 1 FROM {table} LIMIT 0"))
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="validate database and scope only")
    mode.add_argument("--food-id", action="append", help="run a targeted update with its own key")
    parser.add_argument("--fallback-only", action="store_true", help="query only Uni-Prosperity for the given Food_IDs")
    parser.add_argument("--run-key", help="reuse the same key to resume a targeted update")
    args = parser.parse_args()
    if (args.fallback_only or args.run_key is not None) and not args.food_id:
        parser.error("--fallback-only and --run-key require --food-id")

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "backend"))
    from dotenv import dotenv_values

    local_env = dotenv_values(ROOT / ".env")
    for name in (*PRICE_ENV, "PROJECT_DATA_ROOT"):
        if local_env.get(name):
            os.environ.setdefault(name, local_env[name])

    log_path = ROOT / "logs/t02-update.log"
    log_path.parent.mkdir(exist_ok=True)
    logger = logging.getLogger("t02_update")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

    def report(**details):
        payload = json.dumps(details, ensure_ascii=False)
        logger.info(payload)
        if sys.stdout is not None:
            print(payload, flush=True)

    try:
        from app.core.config import settings
        from app.db.session import engine
        from app.pricing_scheduler import SchedulerSettings, create_service, scheduled_run
        from app.source_adapters.ingredient_database import load_ingredient_queries

        release = preflight(engine, settings.generation_recipe_release_id)
        pricing = replace(SchedulerSettings.from_env(), batch_size=None)
        if pricing.enabled:
            raise SystemExit("Disable PRICE_SCHEDULER_ENABLED before using the standalone T02 task")
        if pricing.timezone_name != "Asia/Taipei":
            raise SystemExit("Standalone T02 task requires PRICE_SCHEDULER_TIMEZONE=Asia/Taipei")
        service = create_service(pricing, expected_recipe_release_id=release)
        scope = [query for query in load_ingredient_queries(engine)
                 if not set(query.food_ids) <= service.exempt_food_ids
                 and not set(query.food_ids) <= service.seasoning_food_ids]
        eligible = {food_id for query in scope for food_id in query.food_ids}
        selected = set(args.food_id or ())
        if not eligible or selected - eligible:
            raise SystemExit("No eligible ingredients, or a requested Food_ID is outside the price scope")
        report(event="check" if args.check else "started", recipe_release_id=release,
               eligible_names=len(scope), eligible_food_ids=len(eligible), food_ids=sorted(selected))
        if args.check:
            return 0
        if selected:
            collect = service.run_fallback if args.fallback_only else service.run
            prefix = "fallback-targeted" if args.fallback_only else "pxmart-targeted"
            result = collect(trigger_type="manual", scheduled_for=datetime.now(pricing.timezone),
                             run_key=args.run_key or f"{prefix}-{uuid4()}", food_ids=selected)
        else:
            result = scheduled_run(service, pricing)
        report(event="finished", **asdict(result))
        results = [result, result.fallback] if result.fallback else [result]
        return int(any(run.status in {"failed", "partial"} for run in results))
    except SystemExit as exc:
        report(event="rejected", reason=str(exc))
        return 2
    except Exception as exc:
        # Connection/configuration exceptions can contain local credentials.
        report(event="failed", error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
