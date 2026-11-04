"""T10 獨立工作程序：python -m app.jobs.worker

每輪以短交易領取一個工作，在交易外執行處理函式；處理期間只透過
JobContext 更新進度與發出付費呼叫。可同時啟動多個工作程序。
"""
import os
import signal
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import BackgroundJob, MealRequestRevision
from app.services import jobs, spend


class LeaseLost(Exception):
    """租約已被回收（例如逾時），本工作程序必須停止，不得寫入結果。"""


@dataclass
class JobContext:
    factory: sessionmaker
    job: BackgroundJob
    owner: str

    def stage(self, name: str) -> None:
        if not jobs.set_stage(self.factory, self.job.id, self.owner, name):
            raise LeaseLost(name)

    def input_payload(self) -> dict:
        """生成只讀取送出時鎖定的問卷版本，不讀之後的修改。"""
        with self.factory() as db:
            revision = db.get(MealRequestRevision, (self.job.meal_request_id, self.job.input_version))
            return dict(revision.payload)

    def paid_call(self, *, name: str, purpose: str, provider: str, model: str,
                  max_input_tokens: int, max_output_tokens: int, call: Callable):
        # 呼叫鍵含嘗試次數：同一次嘗試內不可能重複預留同一呼叫
        key = f"{self.job.id}:{self.job.attempts}:{name}"
        return spend.paid_call(
            self.factory, call_key=key, purpose=purpose, provider=provider, model=model,
            max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens, call=call,
            job_id=self.job.id, household_id=self.job.household_id,
        )


def generation_not_available(ctx: JobContext) -> dict:
    """T14 完成前的生成處理：明確回報未開放，不假裝產生菜單、不呼叫付費服務。"""
    ctx.stage("validating_input")
    ctx.input_payload()
    raise jobs.JobFailure(
        "GENERATION_NOT_AVAILABLE",
        "菜單生成功能尚未開放（待 Ticket 14）；問卷已保留，開放後可重新送出。",
    )


HANDLERS: dict[str, Callable[[JobContext], dict]] = {
    jobs.GENERATION: generation_not_available,
}


def run_once(factory: sessionmaker, owner: str, handlers: dict | None = None) -> BackgroundJob | None:
    handlers = handlers or HANDLERS
    job = jobs.claim(factory, owner)
    if job is None:
        return None
    ctx = JobContext(factory, job, owner)
    handler = handlers.get(job.kind)
    try:
        if handler is None:
            raise jobs.JobFailure("GENERATION_FAILED", f"Unknown job kind {job.kind}")
        result = handler(ctx)
    except LeaseLost:
        return job  # 已由恢復流程接手
    except jobs.JobFailure as exc:
        jobs.fail(factory, job.id, owner, code=exc.code, message=exc.message, retryable=False)
    except spend.BudgetExhausted:
        jobs.fail(factory, job.id, owner, code="SERVICE_BUDGET_EXHAUSTED",
                  message="本月外部服務額度已用完，已停止生成；問卷已保留。", retryable=False)
    except spend.UncertainBilling:
        jobs.fail(factory, job.id, owner, code="NEEDS_MANUAL_RETRY",
                  message="外部呼叫結果不明，為避免重複計費不自動重送；問卷已保留，請確認後重新送出。",
                  retryable=False)
    except (jobs.RetryableJobError, spend.CallNotBilled, TimeoutError) as exc:
        # TimeoutError 只在付費呼叫之外發生（付費呼叫的逾時會轉成 UncertainBilling）
        jobs.fail(factory, job.id, owner, code="SERVICE_TIMEOUT" if isinstance(exc, TimeoutError) else "GENERATION_FAILED",
                  message="暫時性錯誤，系統會自動重試一次。", retryable=True)
    except Exception:
        jobs.fail(factory, job.id, owner, code="GENERATION_FAILED",
                  message="生成過程發生錯誤；問卷已保留，可重新送出。", retryable=False)
    else:
        jobs.complete(factory, job.id, owner, result)
    return job


def main() -> None:
    from app.db.session import SessionLocal

    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        if run_once(SessionLocal, owner) is None:
            time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
