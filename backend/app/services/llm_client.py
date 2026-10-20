"""OpenAI 轉接器（T11 向量化；T14 Responses API＋Structured Outputs）。

轉接器只負責 HTTP 與計費判斷，必須包在 spend.paid_call（或 JobContext.paid_call）內使用：
- 回傳 (結果, {"input_tokens", "output_tokens"}) 供結算；
- 確定未計費（連線失敗、請求被拒 4xx）拋 spend.CallNotBilled，可安全重試；
- 其他失敗（逾時、5xx、格式不符）一律視為可能已計費，交由 paid_call 標為 uncertain。
不記錄金鑰、輸入文字或模型原始回應。
"""
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services.spend import CallNotBilled


class ProviderError(Exception):
    """供應商回應異常且無法確定是否計費。"""


def _post(url: str, *, api_key: str, payload: dict, timeout: float) -> httpx.Response:
    """送出請求並依 HTTP 狀態判斷是否計費：確定未計費拋 CallNotBilled，其他非 200 拋 ProviderError。"""
    try:
        res = httpx.post(url, json=payload, timeout=timeout, headers={"Authorization": f"Bearer {api_key}"})
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise CallNotBilled(type(exc).__name__) from None
    if 400 <= res.status_code < 500 and res.status_code != 408:
        raise CallNotBilled(f"HTTP {res.status_code}")
    if res.status_code != 200:
        raise ProviderError(f"HTTP {res.status_code}")
    return res


class OpenAIEmbedder:
    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def embed(self, texts: list[str]) -> tuple[list[list[float]], dict]:
        res = _post(f"{self.base_url}/embeddings", api_key=self.api_key,
                    payload={"model": self.model, "input": list(texts)}, timeout=self.timeout)
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


# --- T14：Responses API＋Structured Outputs ---

@dataclass(frozen=True)
class StructuredResponse:
    """模型回應狀態：completed（有 output_text）、refused（拒答）、incomplete（截斷等）。

    output_text 未經驗證；呼叫端必須以 Pydantic 模型解析，格式不符一律視為無效候選。
    """
    status: str
    output_text: str | None = None
    refusal: str | None = None
    incomplete_reason: str | None = None


class OpenAIResponder:
    def __init__(self, *, api_key: str, base_url: str, timeout: float):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def respond(self, *, model: str, instructions: str, input_text: str, schema_name: str, schema: dict,
                max_output_tokens: int) -> tuple[StructuredResponse, dict]:
        payload = {
            "model": model, "instructions": instructions, "input": input_text,
            "max_output_tokens": max_output_tokens, "store": False,
            "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
        }
        res = _post(f"{self.base_url}/responses", api_key=self.api_key, payload=payload, timeout=self.timeout)
        try:
            body = res.json()
            status = body["status"]
            usage = {"input_tokens": int(body["usage"]["input_tokens"]),
                     "output_tokens": int(body["usage"]["output_tokens"])}
            content = [c for item in body.get("output") or [] if item.get("type") == "message"
                       for c in item.get("content") or []]
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ProviderError("INVALID_RESPONSE") from None
        refusal = next((c.get("refusal") for c in content if c.get("type") == "refusal"), None)
        text = "".join(c.get("text") or "" for c in content if c.get("type") == "output_text")
        if refusal is not None:
            return StructuredResponse("refused", refusal=refusal), usage
        if status == "incomplete":
            reason = (body.get("incomplete_details") or {}).get("reason") or "unknown"
            return StructuredResponse("incomplete", incomplete_reason=reason), usage
        if status != "completed":
            raise ProviderError(f"STATUS_{status}")
        return StructuredResponse("completed", output_text=text), usage


def default_responder() -> OpenAIResponder | None:
    if not settings.openai_api_key:
        return None
    return OpenAIResponder(api_key=settings.openai_api_key, base_url=settings.openai_base_url,
                           timeout=settings.generation_timeout_seconds)
