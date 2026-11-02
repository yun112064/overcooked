"""有禮貌的 HTTP 抓取：只抓指定網站、遵守 robots.txt、每請求間隔 ≥2 秒、可識別 User-Agent、重試退避。

傳輸可注入（測試用假傳輸，絕不連網）：transport(url, headers, timeout) -> Response，
連線錯誤以 OSError／ConnectionError／TimeoutError 表示。
"""
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

MIN_INTERVAL = 2.0
DEFAULT_USER_AGENT = ("overcooked-meal-planner-knowledge/1.0 "
                      "(+https://github.com/yun112064/overcooked; nutrition knowledge with written permission)")


@dataclass
class Response:
    status: int
    text: str


class FetchError(Exception):
    """重試後仍失敗，或 robots.txt 無法取得（保守起見不抓）。"""


class RobotsDisallowed(FetchError):
    pass


def urllib_transport(url: str, headers: dict, timeout: float) -> Response:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 — 只抓白名單網站
            charset = res.headers.get_content_charset() or "utf-8"
            return Response(res.status, res.read().decode(charset, errors="replace"))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, "")
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc.reason)) from None


class PoliteFetcher:
    def __init__(self, transport=urllib_transport, *, user_agent: str = DEFAULT_USER_AGENT,
                 allowed_host: str = "www.healthnews.com.tw", min_interval: float = MIN_INTERVAL,
                 retries: int = 3, backoff: float = 5.0, timeout: float = 20.0,
                 clock=time.monotonic, sleep=time.sleep):
        if min_interval < MIN_INTERVAL:
            raise ValueError(f"min_interval must be >= {MIN_INTERVAL} seconds")
        if not user_agent.strip():
            raise ValueError("An identifiable User-Agent is required")
        self.transport, self.user_agent, self.allowed_host = transport, user_agent, allowed_host
        self.min_interval, self.retries, self.backoff, self.timeout = min_interval, retries, backoff, timeout
        self.clock, self.sleep = clock, sleep
        self._last: float | None = None
        self._robots: RobotFileParser | None = None
        self.requests = 0

    def _wait(self, at_least: float = 0.0) -> None:
        if self._last is not None:
            remaining = max(self.min_interval, at_least) - (self.clock() - self._last)
            if remaining > 0:
                self.sleep(remaining)

    def _request(self, url: str) -> Response:
        """單一網址：限速＋重試；回傳最後的回應（404 等不重試）。"""
        delay = 0.0
        for attempt in range(self.retries + 1):
            self._wait(delay)
            self._last = self.clock()
            self.requests += 1
            try:
                res = self.transport(url, {"User-Agent": self.user_agent, "Accept": "text/html,text/plain"},
                                     self.timeout)
            except (OSError, ConnectionError, TimeoutError):
                res = None
            if res is not None and res.status != 429 and res.status < 500:
                return res
            delay = self.backoff * (2 ** attempt)
        raise FetchError(f"Failed after {self.retries + 1} attempts: {url}")

    def _robots_parser(self) -> RobotFileParser:
        if self._robots is None:
            res = self._request(f"https://{self.allowed_host}/robots.txt")
            parser = RobotFileParser()
            if res.status == 200:
                parser.parse(res.text.splitlines())
            elif 400 <= res.status < 500:
                parser.allow_all = True
            else:
                raise FetchError(f"robots.txt unavailable ({res.status})")
            self._robots = parser
        return self._robots

    def get(self, url: str) -> str | None:
        """回傳 HTML；404／410 回傳 None。非白名單網站拋 ValueError，robots 禁止拋 RobotsDisallowed。"""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != self.allowed_host:
            raise ValueError(f"Only https://{self.allowed_host}/ may be fetched")
        if not self._robots_parser().can_fetch(self.user_agent, url):
            raise RobotsDisallowed(url)
        res = self._request(url)
        if res.status in (404, 410):
            return None
        if res.status != 200:
            raise FetchError(f"Unexpected status {res.status}: {url}")
        return res.text
