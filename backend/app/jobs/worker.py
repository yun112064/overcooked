"""T10 獨立工作程序：python -m app.jobs.worker

每輪以短交易領取一個工作，在交易外執行處理函式；處理期間只透過
JobContext 更新進度與發出付費呼叫。可同時啟動多個工作程序。

每次嘗試有硬性時間上限（job_attempt_timeout_seconds）：計時器到時把工作標為逾時失敗、解鎖問卷，
再直接結束程序，真正中止卡住的計算（Python 執行緒無法從外部停止）。所以 `python -m app.jobs.worker`
是看守程序，實際工作在子程序（--child）執行，子程序結束後自動重啟（2026-10-28：選菜窮舉卡死工作者）。
"""
import os
import signal
import subprocess
import sys
import threading
import traceback
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


def error_site(exc: BaseException) -> str:
    """例外在專案程式碼中最內層的位置（相對路徑:函式:行號）；不含訊息內容。"""
    frames = traceback.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "site-packages" not in f.filename and "/lib/python" not in f.filename.replace("\\", "/")]
    frame = (ours or frames)[-1] if (ours or frames) else None
    if frame is None:
        return "unknown.py:unknown:0"
    path = frame.filename.replace("\\", "/")
    for marker in ("/backend/", "/app/"):
        if marker in path:
            path = path.split(marker, 1)[1]
            break
    return f"{path}:{frame.name}:{frame.lineno}"


def log_unexpected(job, exc: BaseException) -> None:
    """未預期錯誤寫入 process.jsonl：工作 ID、例外類別、發生位置（demo 曾因缺設定檔全數失敗卻無從查起）。"""
    jobs.process_logger.error("job_unexpected_error", extra={
        "event": "job_unexpected_error", "job_id": str(job.id),
        "error_type": type(exc).__qualname__, "error_site": error_site(exc)})


# 嘗試逾時後子程序的結束碼：看守程序據此記錄並重啟
EXIT_ATTEMPT_TIMEOUT = 75


def _exit_after_timeout(job) -> None:
    os._exit(EXIT_ATTEMPT_TIMEOUT)


def run_once(factory: sessionmaker, owner: str, handlers: dict | None = None, *,
             timeout: float | None = None, on_timeout: Callable = _exit_after_timeout) -> BackgroundJob | None:
    """領取並執行一個工作。timeout 秒內未結束：標為逾時失敗後呼叫 on_timeout（預設結束程序）。"""
    handlers = handlers or HANDLERS
    job = jobs.claim(factory, owner)
    if job is None:
        return None
    timer = None
    if timeout:
        def expire():
            # 工作已完成、取消或被回收時 abort_attempt 回 False，不必中止
            if jobs.abort_attempt(factory, job.id, owner):
                on_timeout(job)
        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        timer.start()
    try:
        return _run(factory, owner, handlers, job)
    finally:
        if timer is not None:
            timer.cancel()


def _run(factory: sessionmaker, owner: str, handlers: dict, job: BackgroundJob) -> BackgroundJob:
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
    except Exception as exc:
        log_unexpected(job, exc)
        jobs.fail(factory, job.id, owner, code="GENERATION_FAILED",
                  message="生成過程發生錯誤；問卷已保留，可重新送出。", retryable=False)
    else:
        # 處理函式可附 persist（例如 T14 生成結果表），與完成狀態在同一交易寫入
        jobs.complete(factory, job.id, owner, result, persist=getattr(handler, "persist", None))
    return job


def _exit_when_supervisor_dies() -> None:
    """看守程序以 stdin 管線連著子程序：管線關閉（看守程序結束，含 Windows 上被強制結束）就跟著結束，
    不留下沒人管的工作程序。"""
    def watch():
        try:
            sys.stdin.buffer.read()
        except (OSError, ValueError):
            pass
        os._exit(0)
    threading.Thread(target=watch, daemon=True).start()


def child_main() -> None:
    from app.db.session import SessionLocal

    _exit_when_supervisor_dies()

    owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        if run_once(SessionLocal, owner, timeout=settings.job_attempt_timeout_seconds) is None:
            time.sleep(settings.worker_poll_seconds)


def main() -> None:
    """看守程序：啟動工作子程序，子程序因嘗試逾時（或意外）結束時重啟；收到停止訊號時一併停止子程序。"""
    stopping = False
    child: subprocess.Popen | None = None

    def stop(*_):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        child = subprocess.Popen([sys.executable, "-m", "app.jobs.worker", "--child"], stdin=subprocess.PIPE)
        code = child.wait()
        if stopping:
            break
        jobs.process_logger.warning("worker_child_exited", extra={"event": "worker_child_exited", "exit_code": code})
        time.sleep(1)


if __name__ == "__main__":
    child_main() if "--child" in sys.argv else main()
