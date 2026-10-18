"""T10 單一排程程序：python -m app.jobs.scheduler

以 PostgreSQL advisory lock 保證同時只有一個排程程序在執行，避免多個
API 或排程副本重複做同樣的事。負責：回收逾時工作、每日日誌清理。
"""
import os

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import text

from app.core.log_cleanup import cleanup
from app.core.logging_config import scheduler_logger

# 專案固定的 advisory lock 識別碼
SCHEDULER_LOCK_ID = 74_070_610


class SingletonLock:
    """持有 advisory lock 的專用連線；程序結束或連線中斷時資料庫會自動釋放。"""

    def __init__(self, conn):
        self._conn = conn

    def release(self) -> None:
        try:
            self._conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": SCHEDULER_LOCK_ID})
            self._conn.commit()
        finally:
            # 不放回連線池，避免鎖跟著被重用的連線留下
            self._conn.invalidate()
            self._conn.close()


def acquire_singleton(engine) -> SingletonLock | None:
    """已有其他排程程序時回傳 None。"""
    conn = engine.connect()
    got = conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": SCHEDULER_LOCK_ID}).scalar_one()
    conn.commit()
    if not got:
        conn.close()
        return None
    return SingletonLock(conn)


def build_scheduler(factory) -> BlockingScheduler:
    from app.services import jobs

    scheduler = BlockingScheduler(timezone="Asia/Taipei")
    scheduler.add_job(lambda: jobs.recover_expired(factory), "interval", seconds=60,
                      id="recover_expired_jobs", max_instances=1, coalesce=True)
    apply = os.getenv("LOG_CLEANUP_APPLY", "false").lower() == "true"
    scheduler.add_job(lambda: cleanup(apply=apply), "cron", hour=3, minute=30,
                      id="log_cleanup", max_instances=1, coalesce=True)
    return scheduler


def main() -> None:
    from app.db.session import SessionLocal, engine
    from app.services import jobs

    lock = acquire_singleton(engine)
    if lock is None:
        scheduler_logger.warning("Scheduler already running",
                                 extra={"event": "scheduler_lock_held", "source": "scheduler"})
        raise SystemExit("Another scheduler instance holds the lock; exiting.")
    scheduler_logger.info("Scheduler started", extra={"event": "scheduler_started", "source": "scheduler"})
    # 啟動時先回收一次，處理上次停機留下的工作
    jobs.recover_expired(SessionLocal)
    try:
        build_scheduler(SessionLocal).start()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
