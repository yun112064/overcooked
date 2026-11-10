"""R14 飲食小幫手：雲端把使用者的問題轉送到團隊電腦的 assistant_rag 服務，串流回傳 SSE。

- 驗證與 T17 相同：Cloudflare Access 服務權杖＋服務 Bearer token（`team_link.settings_credentials`）。
- 先等團隊電腦回應狀態碼再開始串流：未設定、連不上、逾時、驗證失敗都回明確的不可用原因，不捏造回答。
- 回答內容（SSE 事件）原樣轉送，不改寫、不記錄；日誌只記原因代碼。
- 團隊電腦用本機模型，不經 T10 額度；之後若改用 OpenAI，必須在這一層加上 T10 預留與結算。
"""
import logging
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass
from threading import Lock

import httpx

from app.services.team_link import TeamCredentials, failure_reason, settings_credentials

log = logging.getLogger("app.assistant")

MAX_MESSAGE = 500
MAX_HISTORY = 6
MAX_HISTORY_CONTENT = 2000


class AssistantUnavailable(Exception):
    """團隊電腦的客服服務不可用；reason 為穩定代碼。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class AssistantRejected(Exception):
    """團隊電腦判定輸入格式錯誤（422）。"""


@dataclass
class AssistantProxy:
    url: str | None
    credentials: TeamCredentials
    connect_timeout: float = 5.0
    read_timeout: float = 120.0
    transport: httpx.BaseTransport | None = None

    def open(self, message: str, history: list[dict]) -> Iterator[bytes]:
        """送出問題；成功時回傳 SSE 位元組串流（呼叫端負責迭代完畢），失敗時丟出 AssistantUnavailable／Rejected。"""
        if not self.url:
            raise AssistantUnavailable("NOT_CONFIGURED")
        client = httpx.Client(transport=self.transport,
                              timeout=httpx.Timeout(self.read_timeout, connect=self.connect_timeout))
        request = client.build_request("POST", f"{self.url.rstrip('/')}/v1/chat",
                                       json={"message": message, "history": history},
                                       headers={**self.credentials.headers(), "Accept": "text/event-stream"})
        try:
            response = client.send(request, stream=True)
        except httpx.TimeoutException:
            client.close()
            raise AssistantUnavailable("TIMEOUT") from None
        except httpx.HTTPError:
            client.close()
            raise AssistantUnavailable("UNREACHABLE") from None
        if response.status_code != 200:
            status = response.status_code
            response.close()
            client.close()
            if status == 422:
                raise AssistantRejected()
            reason = "SERVICE_NOT_READY" if status == 503 else failure_reason(status)
            log.warning("assistant_unavailable", extra={"event": "assistant_unavailable", "reason": reason})
            raise AssistantUnavailable(reason or "SERVICE_ERROR")
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            response.close()
            client.close()
            raise AssistantUnavailable("INVALID_RESPONSE")
        return self._relay(client, response)

    @staticmethod
    def _relay(client: httpx.Client, response: httpx.Response) -> Iterator[bytes]:
        try:
            yield from response.iter_bytes()
        except httpx.HTTPError:
            # 串流中斷：補一個 error 事件，讓前端顯示失敗而不是停在半句
            log.warning("assistant_stream_broken", extra={"event": "assistant_stream_broken"})
            yield b'event: error\ndata: {"code": "ASSISTANT_STREAM_BROKEN"}\n\n'
        finally:
            response.close()
            client.close()


class RateLimiter:
    """每位使用者每分鐘的提問上限（程序內；保護團隊電腦的本機模型）。"""

    def __init__(self, per_minute: int, *, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = self.clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


def default_proxy() -> AssistantProxy:
    from app.core.config import settings
    return AssistantProxy(settings.assistant_service_url, settings_credentials(settings.assistant_service_token),
                          read_timeout=settings.assistant_timeout_seconds)


def openai_proxy() -> AssistantProxy:
    """OpenAI 版小幫手：同一台團隊電腦、同一組權杖，只是服務網址不同。"""
    from app.core.config import settings
    return AssistantProxy(settings.assistant_openai_service_url,
                          settings_credentials(settings.assistant_service_token),
                          read_timeout=settings.assistant_timeout_seconds)
