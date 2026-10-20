"""團隊電腦營養知識 RAG 服務（T11）：python -m knowledge_rag.server

只用標準函式庫 HTTP 伺服器，資料在團隊電腦的 PostgreSQL＋pgvector。
對外只透過加密通道（T17）提供給雲端後端，並以 Bearer token 驗證。
本服務不呼叫付費 API：查詢向量由雲端在額度預留後產生並送來。

  GET  /v1/health            → active 索引版本與向量模型；無索引或庫故障回 503
  POST /v1/knowledge/search  → {food_ids, query_embedding, embedding_model, limit_per_ingredient}
                               回傳 {status, records, by_ingredient, index_version, embedding_model}

環境變數：
  KNOWLEDGE_DATABASE_URL  必填，例如 postgresql://user:pw@127.0.0.1:5432/meal_knowledge
  KNOWLEDGE_TOKEN         必填，與雲端 KNOWLEDGE_SERVICE_TOKEN 相同
  KNOWLEDGE_HOST／KNOWLEDGE_PORT（預設 127.0.0.1:8766）
"""
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from knowledge_rag.store import KnowledgeStore, KnowledgeStoreError

MAX_BYTES = 256 * 1024


def make_handler(store: KnowledgeStore, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "meal-planner-knowledge/1"

        def log_message(self, fmt, *args):  # 不記錄請求內容
            pass

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}")

        def do_GET(self):
            if urlsplit(self.path).path != "/v1/health":
                return self._send(404, {"error": "not_found"})
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            try:
                index = store.active_index()
            except KnowledgeStoreError as exc:
                return self._send(503, {"error": exc.code})
            if index is None:
                return self._send(503, {"error": "NO_ACTIVE_INDEX"})
            self._send(200, {"status": "ok", "index_version": index["index_version"],
                             "embedding_model": index["embedding_model"]})

        def do_POST(self):
            if urlsplit(self.path).path != "/v1/knowledge/search":
                return self._send(404, {"error": "not_found"})
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BYTES:
                return self._send(413 if length > MAX_BYTES else 400, {"error": "invalid_body"})
            try:
                payload = json.loads(self.rfile.read(length))
                result = store.search(payload["food_ids"], payload["query_embedding"], payload["embedding_model"],
                                      limit_per_ingredient=payload.get("limit_per_ingredient", 3))
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": "invalid_input"})
            except KnowledgeStoreError as exc:
                status = 409 if exc.code == "EMBEDDING_MODEL_MISMATCH" else 503
                return self._send(status, {"error": exc.code})
            self._send(200, result)

    return Handler


def main() -> None:
    url = os.environ.get("KNOWLEDGE_DATABASE_URL")
    token = os.environ.get("KNOWLEDGE_TOKEN")
    if not url or not token:
        raise SystemExit("KNOWLEDGE_DATABASE_URL and KNOWLEDGE_TOKEN are required")
    host = os.environ.get("KNOWLEDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("KNOWLEDGE_PORT", "8766"))
    server = ThreadingHTTPServer((host, port), make_handler(KnowledgeStore(url), token))
    print(f"knowledge service on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
