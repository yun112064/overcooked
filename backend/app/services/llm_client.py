"""OpenAI 轉接器（T11 向量化；T14 可在此加入 Responses API）。

轉接器只負責 HTTP 與計費判斷，必須包在 spend.paid_call（或 JobContext.paid_call）內使用：
- 回傳 (結果, {"input_tokens", "output_tokens"}) 供結算；
- 確定未計費（連線失敗、請求被拒 4xx）拋 spend.CallNotBilled，可安全重試；
- 其他失敗（逾時、5xx、格式不符）一律視為可能已計費，交由 paid_call 標為 uncertain。
不記錄金鑰、輸入文字或模型原始回應。
"""
import httpx

from app.core.config import settings
from app.services.spend import CallNotBilled


class ProviderError(Exception):
    """供應商回應異常且無法確定是否計費。"""


class OpenAIEmbedder:
    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def embed(self, texts: list[str]) -> tuple[list[list[float]], dict]:
        try:
            res = httpx.post(f"{self.base_url}/embeddings", timeout=self.timeout,
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             json={"model": self.model, "input": list(texts)})
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise CallNotBilled(type(exc).__name__) from None
        if 400 <= res.status_code < 500 and res.status_code != 408:
            raise CallNotBilled(f"HTTP {res.status_code}")
        if res.status_code != 200:
            raise ProviderError(f"HTTP {res.status_code}")
        try:
            body = res.json()
            rows = sorted(body["data"], key=lambda r: r["index"])
            vectors = [[float(v) for v in r["embedding"]] for r in rows]
            tokens = int(body["usage"]["prompt_tokens"])
        except (KeyError, TypeError, ValueError):
            raise ProviderError("INVALID_RESPONSE") from None
        if len(vectors) != len(texts):
            raise ProviderError("INVALID_RESPONSE")
        return vectors, {"input_tokens": tokens, "output_tokens": 0}


def default_embedder() -> OpenAIEmbedder | None:
    if not settings.openai_api_key:
        return None
    return OpenAIEmbedder(api_key=settings.openai_api_key, model=settings.embedding_model,
                          base_url=settings.openai_base_url, timeout=settings.openai_timeout_seconds)
