import re
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


DATA_ROOT = Path(
    os.getenv("PROJECT_DATA_ROOT", "/app/data")
)

LOG_DIR = DATA_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)



BEARER_PATTERN = re.compile(
    r"\bBearer\s+\S+",
    re.IGNORECASE,
)



SENSITIVE_PATTERN = re.compile(
    r"""(?ix)
    (?<![\w])
    (?P<key>["']?
        (?:password|passwd|pwd|
           token|access_token|refresh_token|
           accessToken|refreshToken|
           api_key|apiKey|
           secret|session|session_id|sessionId|
           authorization|cookie|set-cookie)
    ["']?)
    (?P<sep>\s*[:=]\s*)
    (?P<value>"[^"]*"|'[^']*'|[^\s,;&}\]]+)
    """
)



def redact_sensitive(message: str) -> str:
    message = BEARER_PATTERN.sub(
        "Bearer [REDACTED]", message
    )

    def replace_secret(match):
        value = match.group("value")


        if value.startswith('"') and value.endswith('"'):
            replacement = '"[REDACTED]"'
        elif value.startswith("'") and value.endswith("'"):
            replacement = "'[REDACTED]'"
        else:
            replacement = "[REDACTED]"

        return (
            match.group("key")
            + match.group("sep")
            + replacement
        )

    return SENSITIVE_PATTERN.sub(
        replace_secret,
        message,
    )

SAFE_LOG_EVENTS = {
    "health_check": (
        "api",
        "Health check succeeded",
    ),
    "db_health_check_failed": (
        "api",
        "Database health check failed",
    ),
    "auth_login_failed": (
        "api",
        "Login failed",
    ),
    "log_cleanup_budget_exceeded": (
        "scheduler",
        "Log space budget exceeded after cleanup",
    ),
    "job_claimed": ("process", "Background job claimed"),
    "job_succeeded": ("process", "Background job succeeded"),
    "job_failed": ("process", "Background job failed"),
    "job_retry_scheduled": ("process", "Background job retry scheduled"),
    "job_recovered": ("scheduler", "Expired background job recovered"),
    "scheduler_started": ("scheduler", "Scheduler started"),
    "scheduler_lock_held": ("scheduler", "Another scheduler instance is running"),
    "price_collection_started": (
        "scheduler",
        "Price collection started",
    ),
    "price_collection_completed": (
        "scheduler",
        "Price collection completed",
    ),
    "price_collection_failed": (
        "scheduler",
        "Price collection failed",
    ),
    "price_collection_skipped": (
        "scheduler",
        "Duplicate or concurrent price collection skipped",
    ),
    "knowledge_queue_unavailable": ("process", "Knowledge crawl queue unavailable; skipped"),
}

# 可選追蹤欄位：只接受符合格式的值，避免任意內容寫入正式 Log
SAFE_OPTIONAL_FIELDS = {
    "job_id": re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
    "trace_id": re.compile(r"^[0-9a-f]{16,32}$"),
    "error_code": re.compile(r"^[A-Z][A-Z_]{1,49}$"),
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        raw_event = getattr(record, "event", None)

        approved = (
            SAFE_LOG_EVENTS.get(raw_event)
            if isinstance(raw_event, str)
            else None
        )

        if approved is None:
            event = "unknown_event"
            source = "system"
            message = "Unapproved log message omitted"
        else:
            event = raw_event
            source, message = approved

        log_data = {
            "time": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname,
            "event": event,
            "source": source,
            "message": message,
        }

        if approved is not None:
            for field, pattern in SAFE_OPTIONAL_FIELDS.items():
                value = getattr(record, field, None)
                if isinstance(value, str) and pattern.fullmatch(value):
                    log_data[field] = value
            duration = getattr(record, "duration_ms", None)
            if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0:
                log_data["duration_ms"] = duration

        return json.dumps(
            log_data,
            ensure_ascii=False,
        )



class PreservingRotatingFileHandler(RotatingFileHandler):
    """每日跨日或達大小上限時輪替，不自動刪除舊日誌。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        active_file = Path(self.baseFilename)

        # 重新啟動時，從現有日誌推算目前記錄日期
        if active_file.is_file() and active_file.stat().st_size > 0:
            last_write = active_file.stat().st_mtime
        else:
            last_write = datetime.now(timezone.utc).timestamp()

        self._active_day = datetime.fromtimestamp(
            last_write, timezone.utc
        ).date()
        self._record_day = self._active_day


    def shouldRollover(self, record):
        # 使用 UTC 判斷是否跨日
        self._record_day = datetime.fromtimestamp(
            record.created, timezone.utc
        ).date()

        if self._record_day > self._active_day:
            return True

        # 保留原有的檔案大小判斷
        return super().shouldRollover(record)

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        if (
            os.path.isfile(self.baseFilename)
            and os.path.getsize(self.baseFilename) > 0
        ):
            timestamp = datetime.now(
                timezone.utc
            ).strftime("%Y%m%dT%H%M%S%fZ")

            archive_name = (
                f"{self.baseFilename}.{timestamp}"
            )

            # 同一時間刻度內再次輪替時加序號，避免覆蓋或改名失敗
            suffix = 1
            base_archive = archive_name
            while os.path.exists(archive_name):
                archive_name = f"{base_archive}-{suffix}"
                suffix += 1

            self.rotate(
                self.baseFilename,
                archive_name,
            )

        if not self.delay:
            self.stream = self._open()

        self._active_day = self._record_day

def get_logger(name: str, filename: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        handler = PreservingRotatingFileHandler(
            LOG_DIR / filename,
            maxBytes=10 * 1024 * 1024,
            backupCount=0,
            encoding="utf-8",
            # 第一次寫入才開檔：每個程序只持有自己會寫的日誌檔
            delay=True,
        )

        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)

    return logger


api_logger = get_logger("api", "api.jsonl")
process_logger = get_logger("process", "process.jsonl")
scheduler_logger = get_logger("scheduler", "scheduler.jsonl")
