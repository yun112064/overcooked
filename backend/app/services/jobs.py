"""T10 持久工作：冪等送出、短交易領取、租約與重啟恢復。

狀態對應問卷：queued→queued、running→running、succeeded→result_ready、failed→failed。
每個生成工作最多自動嘗試 max_attempts（預設 2）次；回退或知識說明不重置次數。
"""
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import ApiError
from app.core.config import settings
from app.core.logging_config import process_logger, scheduler_logger
from app.db.models import BackgroundJob, MealRequest, UsageReservation
from app.services import spend

GENERATION = "meal_generation"
MAX_ATTEMPTS = 2
REQUEST_STATUS = {"queued": "queued", "running": "running", "succeeded": "result_ready", "failed": "failed"}


class RetryableJobError(Exception):
    """可在次數內自動重試的失敗（未產生費用，或費用已結算、重試不會重複計費）。

    code／user_message 可選：未達上限時排入重試，達上限即以此錯誤碼與說明失敗。
    """

    def __init__(self, message: str = "", *, code: str | None = None, user_message: str | None = None):
        super().__init__(message)
        self.code = code
        self.user_message = user_message


class JobFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def request_hash(kind: str, meal_request_id, version: int) -> str:
    return hashlib.sha256(f"{kind}:{meal_request_id}:{version}".encode()).hexdigest()


def _log(event: str, job: BackgroundJob, error_code: str | None = None, *, logger=process_logger) -> None:
    # 工作程序寫 process.jsonl、排程寫 scheduler.jsonl，避免多個程序寫同一檔
    extra = {"event": event, "job_id": str(job.id)}
    if error_code:
        extra["error_code"] = error_code
    logger.info(event, extra=extra)


def submit_generation(db: Session, *, household_id, meal_request: MealRequest, version: int,
                      idempotency_key: str, complete: bool) -> tuple[BackgroundJob, bool]:
    """建立生成工作；回傳 (工作, 是否新建立)。呼叫端負責 commit。"""
    digest = request_hash(GENERATION, meal_request.id, version)
    existing = db.scalar(select(BackgroundJob).where(
        BackgroundJob.household_id == household_id, BackgroundJob.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.request_hash != digest:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "這個送出識別碼已用於不同的問卷內容，請重新送出。")
        return existing, False
    if meal_request.version != version:
        raise ApiError(409, "VERSION_CONFLICT", "問卷已更新，請確認最新內容後再送出。")
    if meal_request.status in ("queued", "running"):
        raise ApiError(409, "VERSION_CONFLICT", "此問卷正在生成中，請等待結果。", {"reason": "ALREADY_RUNNING"})
    if not complete:
        raise ApiError(422, "INVALID_INPUT", "問卷尚未填寫完整，無法送出。", {"reason": "INCOMPLETE"})
    since = _now() - timedelta(days=1)
    recent = db.scalar(select(func.count(BackgroundJob.id)).where(
        BackgroundJob.household_id == household_id, BackgroundJob.kind == GENERATION,
        BackgroundJob.created_at >= since))
    if recent >= settings.generation_daily_limit:
        raise ApiError(429, "RATE_LIMITED", f"每個家庭 24 小時內最多送出 {settings.generation_daily_limit} 次生成，請稍後再試。")
    if spend.remaining_twd(db) <= 0:
        raise ApiError(503, "SERVICE_BUDGET_EXHAUSTED",
                       "本月外部服務額度已用完，暫停新的生成；既有紀錄與其他功能仍可使用。")
    job = BackgroundJob(
        household_id=household_id, kind=GENERATION, idempotency_key=idempotency_key, request_hash=digest,
        meal_request_id=meal_request.id, input_version=version, status="queued", attempts=0,
        max_attempts=MAX_ATTEMPTS, available_at=_now(), stage="queued",
    )
    db.add(job)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # 並行送出同一鍵：以先寫入者為準
        raced = db.scalar(select(BackgroundJob).where(
            BackgroundJob.household_id == household_id, BackgroundJob.idempotency_key == idempotency_key))
        if raced is None or raced.request_hash != digest:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "這個送出識別碼已用於不同的問卷內容，請重新送出。") from None
        return raced, False
    meal_request.status = "queued"
    return job, True


def _mirror(db: Session, job: BackgroundJob, status: str | None = None) -> None:
    if job.meal_request_id:
        db.execute(text("UPDATE meal_requests SET status = :s, updated_at = now() WHERE id = :id"),
                   {"s": status or REQUEST_STATUS[job.status], "id": job.meal_request_id})


def claim(factory: sessionmaker, owner: str) -> BackgroundJob | None:
    """短交易領取一個到期工作；SKIP LOCKED 讓多個工作程序不會搶同一筆。"""
    with factory() as db, db.begin():
        job = db.scalar(
            select(BackgroundJob)
            .where(BackgroundJob.status == "queued", BackgroundJob.available_at <= _now())
            .order_by(BackgroundJob.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.lease_owner = owner
        job.lease_expires_at = _now() + timedelta(seconds=settings.job_lease_seconds)
        job.stage = "started"
        job.updated_at = _now()
        _mirror(db, job)
        db.flush()
        db.expunge(job)
    _log("job_claimed", job)
    return job


def _owned(db: Session, job_id, owner: str) -> BackgroundJob | None:
    job = db.scalar(select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update())
    if job is None or job.status != "running" or job.lease_owner != owner:
        return None  # 租約已被回收，結果作廢
    return job


def set_stage(factory: sessionmaker, job_id, owner: str, stage: str) -> bool:
    """更新進度並延長租約；租約已失效時回傳 False，呼叫端應停止。"""
    with factory() as db, db.begin():
        job = _owned(db, job_id, owner)
        if job is None:
            return False
        job.stage = stage
        job.lease_expires_at = _now() + timedelta(seconds=settings.job_lease_seconds)
        job.updated_at = _now()
        return True


def complete(factory: sessionmaker, job_id, owner: str, result: dict, persist=None) -> bool:
    """persist(db, job, result) -> (工作摘要, 結果版本)：在同一交易、確認租約後寫入結果表（T14）。"""
    with factory() as db, db.begin():
        job = _owned(db, job_id, owner)
        if job is None:
            return False
        version = job.attempts
        if persist is not None:
            result, version = persist(db, job, result)
        job.status, job.stage, job.result = "succeeded", "result_ready", result
        job.result_version = version
        job.lease_owner = job.lease_expires_at = None
        job.finished_at = job.updated_at = _now()
        # T15：結果需要使用者決定（缺價／缺必要營養）時問卷進入 awaiting_decision
        _mirror(db, job, result.get("request_status") if isinstance(result, dict) else None)
    _log("job_succeeded", job)
    return True


def fail(factory: sessionmaker, job_id, owner: str, *, code: str, message: str, retryable: bool) -> bool:
    with factory() as db, db.begin():
        job = _owned(db, job_id, owner)
        if job is None:
            return False
        job.error_code, job.error_message = code, message
        job.lease_owner = job.lease_expires_at = None
        job.updated_at = _now()
        if retryable and job.attempts < job.max_attempts:
            job.status, job.stage = "queued", "retry_scheduled"
            job.available_at = _now() + timedelta(seconds=settings.job_retry_backoff_seconds)
        else:
            job.status, job.stage = "failed", "failed"
            job.finished_at = _now()
        _mirror(db, job)
    _log("job_failed" if job.status == "failed" else "job_retry_scheduled", job, code)
    return True


def recover_expired(factory: sessionmaker) -> int:
    """回收租約逾時的工作（程序重啟或當機後）。

    有進行中的付費呼叫（reserved）時，無法確定是否已計費：標為 uncertain、
    工作失敗並要求人工確認後重新送出，不自動重送。
    """
    recovered = 0
    with factory() as db, db.begin():
        jobs = db.scalars(
            select(BackgroundJob)
            .where(BackgroundJob.status == "running", BackgroundJob.lease_expires_at < _now())
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            in_flight = db.scalars(select(UsageReservation).where(
                UsageReservation.job_id == job.id, UsageReservation.status == "reserved")).all()
            for row in in_flight:
                row.status = "uncertain"
                row.note = "worker lease expired during paid call; reconcile with provider usage"
            job.lease_owner = job.lease_expires_at = None
            job.updated_at = _now()
            if in_flight:
                job.status, job.stage = "failed", "needs_manual_retry"
                job.error_code = "NEEDS_MANUAL_RETRY"
                job.error_message = "外部呼叫結果不明，為避免重複計費不自動重送；問卷已保留，請確認後重新送出。"
                job.finished_at = _now()
            elif job.attempts < job.max_attempts:
                job.status, job.stage = "queued", "recovered"
                job.available_at = _now()
            else:
                job.status, job.stage = "failed", "failed"
                job.error_code = "GENERATION_FAILED"
                job.error_message = "工作中斷且已達自動重試上限；問卷已保留，可重新送出。"
                job.finished_at = _now()
            _mirror(db, job)
            recovered += 1
            _log("job_recovered", job, job.error_code, logger=scheduler_logger)
    return recovered


def public_view(job: BackgroundJob) -> dict:
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "stage": job.stage,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "meal_request_id": str(job.meal_request_id) if job.meal_request_id else None,
        "input_version": job.input_version,
        "result_version": job.result_version,
        "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
        # T14：成功的生成工作附結果摘要（結果版本、模式與「AI 生成菜單」標示）；完整結果在 generation_results
        "result_summary": (job.result if job.status == "succeeded" and isinstance(job.result, dict)
                           and "generation_result_id" in job.result else None),
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }
