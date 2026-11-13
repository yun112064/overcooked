"""可替換的模型介面：Embedder（向量）、Reranker（重排序）。回答用的 ChatModel 在步驟 4 加入。

- 地端優先：Ollama 嵌入（embeddinggemma；2026-11-12 使用者決議移除 bge-m3）、Text Embeddings Inference 容器的 bge-reranker-v2-m3。
- 以環境變數切換：RAG_OLLAMA_URL、RAG_RERANK_URL；RAG_LLM_PROVIDER=openai 時回答改由雲端內部 API 呼叫 OpenAI
  （CloudOpenAIChat，經 T10 額度控制 spend.paid_call），不得自動從地端失敗改走付費。
- 嵌入模型綁定 Ollama 回報的 digest；模型換版會讓索引失效，需重建（index_runs 記錄 digest）。
"""
import json
import os
from dataclasses import dataclass
from typing import Protocol

import httpx


class ProviderUnavailable(Exception):
    """模型服務沒開、模型沒安裝或回應異常（code 供上層顯示不可用）。"""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}{': ' + detail if detail else ''}")
        self.code = code


class Embedder(Protocol):
    name: str          # 索引代號：embeddinggemma
    model: str         # 服務端模型名
    dimensions: int
    template: str      # 文件／查詢前綴的版本

    def fingerprint(self) -> str: ...
    def documents(self, texts: list[str], titles: list[str] | None = None) -> list[list[float]]: ...
    def query(self, text: str) -> list[float]: ...


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, texts: list[str]) -> list[float]: ...


@dataclass(frozen=True)
class EmbedSpec:
    name: str
    model: str
    dimensions: int
    template: str
    query_prefix: str = ""
    document_format: str = "{text}"  # 可用 {title}、{text}


EMBEDDERS = {
    # embeddinggemma：官方建議的檢索提示格式
    "embeddinggemma": EmbedSpec("embeddinggemma", "embeddinggemma:latest", 768, "embeddinggemma-retrieval-v1",
                                query_prefix="task: search result | query: ",
                                document_format="title: {title} | text: {text}"),
}


def ollama_url() -> str:
    return os.getenv("RAG_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


class OllamaEmbedder:
    def __init__(self, spec: EmbedSpec, url: str | None = None, *, transport=None, timeout: float = 120):
        self.spec, self.url, self.transport, self.timeout = spec, (url or ollama_url()), transport, timeout
        self.name, self.model, self.dimensions, self.template = spec.name, spec.model, spec.dimensions, spec.template

    def _client(self, timeout=None) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=timeout or self.timeout)

    def fingerprint(self) -> str:
        """Ollama 模型 digest；沒安裝或服務沒開時丟 ProviderUnavailable。"""
        try:
            with self._client(10) as client:
                rows = client.get(self.url + "/api/tags").raise_for_status().json().get("models", [])
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("EMBED_SERVICE_DOWN", str(exc)) from exc
        row = next((r for r in rows if r.get("name") == self.model), None)
        if not row or not isinstance(row.get("digest"), str):
            raise ProviderUnavailable("EMBED_MODEL_NOT_INSTALLED", self.model)
        return row["digest"]

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs or any(not isinstance(t, str) or not t.strip() for t in inputs):
            raise ValueError("嵌入文字不可為空")
        try:
            with self._client() as client:
                body = client.post(self.url + "/api/embed", json={
                    "model": self.model, "input": inputs, "truncate": True, "keep_alive": "10m",
                }).raise_for_status().json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("EMBED_FAILED", str(exc)) from exc
        vectors = body.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(inputs):
            raise ProviderUnavailable("EMBED_BAD_RESPONSE", "數量不符")
        for v in vectors:
            if not isinstance(v, list) or len(v) != self.dimensions:
                raise ProviderUnavailable("EMBED_BAD_RESPONSE", f"維度應為 {self.dimensions}")
        return vectors

    def documents(self, texts, titles=None):
        titles = titles or ["none"] * len(texts)
        return self._embed([self.spec.document_format.format(title=t or "none", text=x) for x, t in zip(texts, titles)])

    def query(self, text):
        return self._embed([self.spec.query_prefix + text])[0]


def embedder(name: str, **kwargs) -> Embedder:
    provider = os.getenv("RAG_EMBED_PROVIDER", "ollama")
    if provider != "ollama":
        raise ProviderUnavailable("EMBED_PROVIDER_NOT_CONFIGURED", provider)  # OpenAI 等之後經 T10 接入
    if name not in EMBEDDERS:
        raise ValueError(f"未知的嵌入模型：{name}（可用：{', '.join(EMBEDDERS)}）")
    return OllamaEmbedder(EMBEDDERS[name], **kwargs)


class TeiReranker:
    """Text Embeddings Inference 的 /rerank（bge-reranker-v2-m3）。回傳與 texts 同順序的分數（0–1）。"""

    name = "bge-reranker-v2-m3"

    def __init__(self, url: str | None = None, *, transport=None, timeout: float = 60, batch: int = 32):
        self.url = (url or os.getenv("RAG_RERANK_URL", "http://127.0.0.1:8092")).rstrip("/")
        self.transport, self.timeout, self.batch = transport, timeout, batch

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        scores = [0.0] * len(texts)
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                for start in range(0, len(texts), self.batch):
                    part = texts[start:start + self.batch]
                    rows = client.post(self.url + "/rerank", json={
                        "query": query, "texts": part, "truncate": True, "raw_scores": False,
                    }).raise_for_status().json()
                    for row in rows:
                        scores[start + row["index"]] = float(row["score"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderUnavailable("RERANK_FAILED", str(exc)) from exc
        return scores


def reranker(**kwargs) -> Reranker:
    provider = os.getenv("RAG_RERANK_PROVIDER", "tei")
    if provider != "tei":
        raise ProviderUnavailable("RERANK_PROVIDER_NOT_CONFIGURED", provider)
    return TeiReranker(**kwargs)


class ChatModel(Protocol):
    """對話模型：問句改寫（步驟 3）、回答與評審（步驟 4、6）共用。"""

    name: str

    def complete(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.0) -> str: ...
    def stream(self, messages: list[dict], *, temperature: float = 0.0): ...  # 產生文字片段


DEFAULT_CHAT_MODEL = "qwen3.5:9b-q4_K_M"


class OllamaChat:
    """Ollama /api/chat；關閉 thinking，非串流。"""

    def __init__(self, model: str | None = None, url: str | None = None, *, transport=None, timeout: float = 180):
        self.name = model or os.getenv("RAG_LLM_MODEL", DEFAULT_CHAT_MODEL)
        self.url, self.transport, self.timeout = (url or ollama_url()), transport, timeout

    def complete(self, messages, *, json_mode=False, temperature=0.0):
        payload = {"model": self.name, "messages": messages, "stream": False, "think": False,
                   "keep_alive": "10m", "options": {"temperature": temperature}}
        if json_mode:
            payload["format"] = "json"
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                body = client.post(self.url + "/api/chat", json=payload).raise_for_status().json()
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("LLM_FAILED", str(exc)) from exc
        content = (body.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ProviderUnavailable("LLM_BAD_RESPONSE", "沒有 message.content")
        return content

    def stream(self, messages, *, temperature=0.0):
        """逐段產生回答文字（Ollama NDJSON 串流）。"""
        payload = {"model": self.name, "messages": messages, "stream": True, "think": False,
                   "keep_alive": "10m", "options": {"temperature": temperature}}
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                with client.stream("POST", self.url + "/api/chat", json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.strip():
                            continue
                        try:
                            part = json.loads(line)
                        except ValueError as exc:
                            raise ProviderUnavailable("LLM_BAD_RESPONSE", "串流格式錯誤") from exc
                        if part.get("error"):
                            raise ProviderUnavailable("LLM_FAILED", str(part["error"]))
                        if (text := (part.get("message") or {}).get("content")):
                            yield text
                        if part.get("done"):
                            return
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("LLM_FAILED", str(exc)) from exc


class CloudOpenAIChat:
    """OpenAI 版：生成文字經雲端內部 API（/internal/assistant/chat）呼叫 OpenAI，受 T10 額度控制。

    團隊電腦不持有 OPENAI_API_KEY；沿用知識庫的雲端內部 API 設定：
    CLOUD_INTERNAL_URL、CLOUD_INTERNAL_TOKEN、CLOUD_ACCESS_CLIENT_ID／CLOUD_ACCESS_CLIENT_SECRET。
    雲端不支援逐字串流：stream() 等完整回答回來後逐句送出。失敗不會自動改用地端模型。
    """

    name = "openai"

    def __init__(self, url: str | None = None, token: str | None = None, *, transport=None, timeout: float = 120):
        self.url = (url or os.getenv("CLOUD_INTERNAL_URL", "")).rstrip("/")
        self.token = token or os.getenv("CLOUD_INTERNAL_TOKEN", "")
        self.transport, self.timeout = transport, timeout
        self.headers = {"Authorization": f"Bearer {self.token}"}
        client_id, secret = os.getenv("CLOUD_ACCESS_CLIENT_ID"), os.getenv("CLOUD_ACCESS_CLIENT_SECRET")
        if client_id and secret:
            self.headers.update({"CF-Access-Client-Id": client_id, "CF-Access-Client-Secret": secret})

    @property
    def configured(self) -> bool:
        return bool(self.url and self.token)

    def complete(self, messages, *, json_mode=False, temperature=0.0):
        if not self.configured:
            raise ProviderUnavailable("LLM_NOT_CONFIGURED", "需要 CLOUD_INTERNAL_URL 與 CLOUD_INTERNAL_TOKEN")
        import uuid
        payload = {"call_id": uuid.uuid4().hex, "messages": messages, "json_mode": json_mode}
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout) as client:
                res = client.post(self.url + "/internal/assistant/chat", json=payload, headers=self.headers)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("LLM_FAILED", type(exc).__name__) from exc
        if res.status_code in (401, 403):
            raise ProviderUnavailable("LLM_AUTH_FAILED")
        try:
            body = res.json()
        except ValueError:
            raise ProviderUnavailable("LLM_BAD_RESPONSE", f"HTTP {res.status_code}") from None
        if res.status_code != 200:
            code = ((body.get("error") or {}).get("code") if isinstance(body, dict) else None) or f"HTTP {res.status_code}"
            raise ProviderUnavailable("LLM_BUDGET_EXHAUSTED" if code == "SERVICE_BUDGET_EXHAUSTED" else "LLM_FAILED", code)
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ProviderUnavailable("LLM_BAD_RESPONSE", "沒有 text")
        return text

    def stream(self, messages, *, temperature=0.0):
        """逐句送出完整回答（保留換行），前端仍是逐段出現。"""
        import re
        text = self.complete(messages, temperature=temperature)
        for piece in re.split(r"(?<=[。！？\n])", text):
            if piece:
                yield piece


def chat_model(**kwargs) -> ChatModel:
    """RAG_LLM_PROVIDER=ollama（預設，地端 qwen3.5）或 openai（經雲端 T10 額度呼叫 OpenAI）。"""
    provider = os.getenv("RAG_LLM_PROVIDER", "ollama")
    if provider == "openai":
        return CloudOpenAIChat(**kwargs)
    if provider != "ollama":
        raise ProviderUnavailable("LLM_PROVIDER_NOT_CONFIGURED", provider)
    return OllamaChat(**kwargs)
