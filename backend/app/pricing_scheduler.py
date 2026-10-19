"""T02 daily PX Mart collection: settings, service factory and manual CLI.

The recurring job is registered in the single scheduler process
(app.jobs.scheduler); this module no longer starts its own scheduler.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.source_adapters.price_collection import PriceCollectionService
from app.source_adapters.pricing_repository import PricingRepository
from app.source_adapters.pxmart import PxMartAdapter


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value}")


@dataclass(frozen=True)
class SchedulerSettings:
    enabled: bool
    hour: int
    minute: int
    timezone_name: str
    workbook_path: Path
    delay_seconds: float
    timeout_seconds: float
    retries: int
    batch_size: int | None = None

    @property
    def timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"unknown PRICE_SCHEDULER_TIMEZONE: {self.timezone_name}"
            ) from exc

    @classmethod
    def from_env(cls) -> "SchedulerSettings":
        settings = cls(
            enabled=_boolean(os.getenv("PRICE_SCHEDULER_ENABLED", "true")),
            hour=int(os.getenv("PRICE_SCHEDULER_HOUR", "3")),
            minute=int(os.getenv("PRICE_SCHEDULER_MINUTE", "0")),
            timezone_name=os.getenv(
                "PRICE_SCHEDULER_TIMEZONE", "Asia/Taipei"
            ),
            workbook_path=Path(
                os.getenv(
                    "PRICE_WORKBOOK_PATH",
                    "data/recipe_dataset_1017.xlsx",
                )
            ),
            delay_seconds=float(
                os.getenv("PRICE_COLLECTION_DELAY_SECONDS", "1.5")
            ),
            timeout_seconds=float(
                os.getenv("PRICE_COLLECTION_TIMEOUT_SECONDS", "15")
            ),
            retries=int(os.getenv("PRICE_COLLECTION_RETRIES", "1")),
            batch_size=(
                int(os.getenv("PRICE_COLLECTION_BATCH_SIZE", "0")) or None
            ),
        )
        if not 0 <= settings.hour <= 23:
            raise ValueError("PRICE_SCHEDULER_HOUR must be 0..23")
        if not 0 <= settings.minute <= 59:
            raise ValueError("PRICE_SCHEDULER_MINUTE must be 0..59")
        if settings.delay_seconds < 0:
            raise ValueError("PRICE_COLLECTION_DELAY_SECONDS cannot be negative")
        if not 0 <= settings.retries <= 3:
            raise ValueError("PRICE_COLLECTION_RETRIES must be 0..3")
        if settings.batch_size is not None and settings.batch_size < 1:
            raise ValueError("PRICE_COLLECTION_BATCH_SIZE must be positive or 0")
        settings.timezone
        return settings


def create_service(settings: SchedulerSettings) -> PriceCollectionService:
    return PriceCollectionService(
        workbook_path=settings.workbook_path,
        repository=PricingRepository(),
        adapter_factory=lambda: PxMartAdapter(
            timeout=settings.timeout_seconds,
            retries=settings.retries,
        ),
        delay_seconds=settings.delay_seconds,
    )


def scheduled_run(
    service: PriceCollectionService,
    settings: SchedulerSettings,
):
    scheduled_for = datetime.now(settings.timezone)
    run_key = f"pxmart-daily-{scheduled_for.date().isoformat()}"
    return service.run(
        trigger_type="scheduled",
        scheduled_for=scheduled_for,
        run_key=run_key,
        batch_size=settings.batch_size,
    )


def build_scheduler(
    service: PriceCollectionService,
    settings: SchedulerSettings,
) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone=settings.timezone)
    scheduler.add_job(
        scheduled_run,
        trigger=CronTrigger(
            hour=settings.hour,
            minute=settings.minute,
            timezone=settings.timezone,
        ),
        id="t02-pxmart-daily-price-collection",
        args=[service, settings],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60 * 60,
    )
    return scheduler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="run once immediately; works even if the recurring scheduler is disabled",
    )
    parser.add_argument(
        "--run-key",
        help="optional idempotency key for a manual run",
    )
    parser.add_argument(
        "--show-runs",
        type=int,
        metavar="N",
        help="print the most recent N persisted collection runs and exit",
    )
    parser.add_argument("--limit", type=int, help="process only the first N ingredients")
    parser.add_argument(
        "--batch-size",
        type=int,
        help="checkpoint after N remaining ingredients; rerun the same key to resume",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")
    if args.batch_size is not None and args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    settings = SchedulerSettings.from_env()
    if args.show_runs is not None:
        runs = PricingRepository().list_recent_runs(args.show_runs)
        print(json.dumps(runs, ensure_ascii=False, default=str, indent=2))
        return 0

    if not args.run_once and not settings.enabled:
        print("T02 price scheduler disabled by PRICE_SCHEDULER_ENABLED", flush=True)
        return 0

    service = create_service(settings)
    if args.run_once:
        result = service.run(
            trigger_type="manual",
            scheduled_for=datetime.now(settings.timezone),
            run_key=args.run_key,
            limit=args.limit,
            batch_size=args.batch_size or settings.batch_size,
        )
        print(json.dumps(asdict(result), ensure_ascii=False), flush=True)
        return 0 if result.status != "failed" else 1

    # 架構只允許一個 APScheduler 程序：長駐排程已併入 app.jobs.scheduler
    print(
        "T02 daily collection runs inside the single scheduler process: "
        "python -m app.jobs.scheduler  (use --run-once for a manual run)",
        flush=True,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
