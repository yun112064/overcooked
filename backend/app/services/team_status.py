"""團隊電腦服務狀態（pre-review #5）：YOLO 辨識與知識 RAG 是否在線，供畫面提示與 QA。

以 GET {url}/v1/health（Access 服務權杖＋Bearer）探測，逾時 3 秒；結果在程序內快取 30 秒，
避免每次開頁都打到團隊電腦。只回傳 online／offline／not_configured 與原因代碼，不回傳網址或憑證。
"""
import time
from collections.abc import Callable

import httpx

from app.core.config import settings
from app.services.team_link import TeamCredentials, failure_reason, settings_credentials

CACHE_SECONDS = 30.0
TIMEOUT_SECONDS = 3.0
_cache: dict[str, tuple[float, dict]] = {}


def probe(url: str | None, credentials: TeamCredentials, *, get: Callable = httpx.get) -> dict:
    if not url:
        return {"status": "not_configured", "reason": "NOT_CONFIGURED"}
    try:
        res = get(f"{url.rstrip('/')}/v1/health", headers=credentials.headers(), timeout=TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        return {"status": "offline", "reason": "TIMEOUT"}
    except httpx.HTTPError:
        return {"status": "offline", "reason": "UNREACHABLE"}
    reason = failure_reason(res.status_code)
    if reason is None:
        return {"status": "online", "reason": None}
    if res.status_code == 503:
        reason = "SERVICE_NOT_READY"  # 例如知識服務沒有 active 索引
    return {"status": "offline", "reason": reason}


def team_status(*, now: Callable[[], float] = time.monotonic, get: Callable = httpx.get) -> dict:
    services = {
        "recognition": (settings.recognition_service_url, settings.recognition_service_token),
        "knowledge": (settings.knowledge_service_url, settings.knowledge_service_token),
    }
    out = {}
    for name, (url, token) in services.items():
        cached = _cache.get(name)
        if cached and now() - cached[0] < CACHE_SECONDS:
            out[name] = cached[1]
            continue
        state = probe(url, settings_credentials(token), get=get)
        _cache[name] = (now(), state)
        out[name] = state
    return out
