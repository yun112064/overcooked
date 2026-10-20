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
from app.services import generation, jobs, result_retry, spend


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


# T14 菜單生成：資料庫菜單優先，查無可行方案自動 AI 生成，最後附知識說明。
# 處理函式與其 persist 只來自 generation.make_generation_handler（單一來源）。
HANDLERS: dict[str, Callable[[JobContext], dict]] = {
    jobs.GENERATION: generation.make_generation_handler(),
    # T15 結果重試：只重試知識說明、缺價定向查價（產生新結果版本）
    jobs.KNOWLEDGE_RETRY: result_retry.make_knowledge_retry_handler(),
    jobs.PRICE_RETRY: result_retry.make_price_retry_handler(),
}

RETRY_MESSAGE = "暫時性錯誤，系統會自動重試一次。"


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
    except jobs.RetryableJobError as exc:
        jobs.fail(factory, job.id, owner, code=exc.code or "GENERATION_FAILED",
                  message=exc.user_message or RETRY_MESSAGE, retryable=True)
    except spend.CallNotBilled:
        jobs.fail(factory, job.id, owner, code="GENERATION_FAILED", message=RETRY_MESSAGE, retryable=True)
    except TimeoutError:
        # 只在付費呼叫之外發生（付費呼叫的逾時會轉成 UncertainBilling）
        jobs.fail(factory, job.id, owner, code="SERVICE_TIMEOUT", message=RETRY_MESSAGE, retryable=True)
    except Exception:
        jobs.fail(factory, job.id, owner, code="GENERATION_FAILED",
                  message="生成過程發生錯誤；問卷已保留，可重新送出。", retryable=False)
    else:
        # 處理函式可附 persist（例如 T14 生成結果表），與完成狀態在同一交易寫入
        jobs.complete(factory, job.id, owner, result, persist=getattr(handler, "persist", None))
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
