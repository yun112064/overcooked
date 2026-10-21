"""團隊電腦 → 雲端內部 API 用戶端（pre-review #6；只用標準函式庫）。

團隊電腦不再持有雲端 DATABASE_URL 或 OPENAI_API_KEY：
- 補抓佇列：認領（claim）／回報（complete）／待處理數（summary）；
- 建索引向量化：每批送到雲端，由雲端經 T10 額度預留後呼叫 OpenAI，回傳向量。
連線經 Cloudflare Tunnel＋Access：帶 Access 服務權杖（CF-Access-Client-Id／Secret）與內部 API Bearer token，
與雲端呼叫團隊電腦的 T17 機制相同。所有失敗都是明確的 CloudError（不當成「沒有待補食材」）。
"""
import json
import urllib.error
import urllib.request
from collections.abc import Callable

TUNNEL_DOWN = {502, 504, 530}


class CloudError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as res:  # noqa: S310 — 固定 https 內部網址
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CloudError("UNREACHABLE", type(exc).__name__) from None


class CloudClient:
    def __init__(self, base_url: str, token: str, *, access_client_id: str | None = None,
                 access_client_secret: str | None = None, timeout: float = 60.0,
                 transport: Callable = urllib_transport):
        if not base_url or not token:
            raise ValueError("cloud internal API URL and token are required")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                        "Accept": "application/json"}
        if access_client_id and access_client_secret:
            self.headers.update({"CF-Access-Client-Id": access_client_id, "CF-Access-Client-Secret": access_client_secret})
        self.timeout = timeout
        self.transport = transport

    def __repr__(self) -> str:
        return f"CloudClient({self.base_url!r})"  # 不含權杖

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        status, raw = self.transport(method, f"{self.base_url}{path}", dict(self.headers), body, self.timeout)
        if status in (401, 403) or 300 <= status < 400:
            raise CloudError("AUTH_FAILED")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            if status in TUNNEL_DOWN:
                raise CloudError("UNREACHABLE", f"HTTP {status}") from None
            raise CloudError("INVALID_RESPONSE") from None
        if status in TUNNEL_DOWN and not (isinstance(data, dict) and isinstance(data.get("error"), dict)
                                          and data["error"].get("code")):
            raise CloudError("UNREACHABLE", f"HTTP {status}")  # 通道錯誤頁；後端自己的 502 帶明確錯誤碼
        if status != 200:
            error = (data.get("error") or {}) if isinstance(data, dict) else {}
            raise CloudError(error.get("code") or "SERVICE_ERROR", error.get("message") or f"HTTP {status}")
        return data

    # --- 補抓佇列 ---

    def claim(self, limit: int) -> list[dict]:
        return self._call("POST", "/internal/knowledge/crawl-requests/claim", {"limit": limit})["requests"]

    def complete(self, outcomes: dict[str, dict]) -> int:
        return self._call("POST", "/internal/knowledge/crawl-requests/complete", {"outcomes": outcomes})["updated"]

    def pending_count(self) -> int:
        return int(self._call("GET", "/internal/knowledge/crawl-requests/summary")["pending"])

    # --- 營養知識分類與萃取（決策 #29） ---

    def classify_batch(self, run_id: str, batch_no: int, records: list[dict]) -> dict:
        """records：[{knowledge_id, source_version, ingredient_name, excerpt}] → {model, prompt_version, items}。"""
        data = self._call("POST", "/internal/knowledge/classify",
                          {"run_id": run_id, "batch_no": batch_no, "records": list(records)})
        if not isinstance(data.get("items"), list) or not data.get("model"):
            raise CloudError("INVALID_RESPONSE")
        return data

    # --- 建索引向量化 ---

    def index_embedder(self, index_version: str, model: str) -> Callable[[list[str]], list[list[float]]]:
        """KnowledgeStore.build_index 用：依序送出各批；雲端回傳的模型必須與索引宣告的模型相同。"""
        batch_no = 0

        def embed(texts: list[str]) -> list[list[float]]:
            nonlocal batch_no
            data = self._call("POST", "/internal/knowledge/embeddings",
                              {"index_version": index_version, "batch_no": batch_no, "texts": list(texts)})
            batch_no += 1
            if data.get("model") != model or len(data.get("vectors") or []) != len(texts):
                raise CloudError("MODEL_MISMATCH", f"cloud embedded with {data.get('model')!r}")
            return data["vectors"]
        return embed
