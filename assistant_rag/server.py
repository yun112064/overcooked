"""團隊電腦上的飲食客服服務（只綁本機；雲端後端經既有加密通道轉送，步驟 7 接上）。

  POST /v1/chat     Bearer 驗證；body {"message": str, "history": [{"role": "user"|"assistant", "content": str}]}
                    回應 text/event-stream：meta → references → delta… → done（或 error）
  GET  /v1/health   Bearer 驗證；資料庫、索引版本、嵌入／重排／LLM 是否可用

  ASSISTANT_SERVICE_TOKEN=… python -m assistant_rag.server [--host 127.0.0.1] [--port 8093]

不記錄問題、回答、權杖或例外文字；日誌只有類型、耗時與數量。
"""
import argparse
import hmac
import json
import logging
import os
import threading

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from assistant_rag.answer import Assistant, InvalidInput

log = logging.getLogger("assistant_rag")


def sse(event: dict) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


def make_app(token: str, factory, health_check=None) -> FastAPI:
    """factory() → Assistant（第一次請求才建立，模型或資料庫沒開時回 503）。"""
    if not token:
        raise ValueError("ASSISTANT_SERVICE_TOKEN required")
    app = FastAPI(title="diet assistant", docs_url=None, redoc_url=None, openapi_url=None)
    state = {"assistant": None}
    lock = threading.Lock()

    def authorize(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization.encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "AUTH_FAILED")

    def assistant() -> Assistant:
        with lock:
            if state["assistant"] is None:
                try:
                    state["assistant"] = factory()
                except Exception:
                    log.warning("assistant_unavailable", extra={"event": "assistant_unavailable"})
                    raise HTTPException(503, "ASSISTANT_UNAVAILABLE") from None
            return state["assistant"]

    @app.post("/v1/chat", dependencies=[Depends(authorize)])
    def chat(body: dict):
        message, history = body.get("message"), body.get("history") or []
        if not isinstance(history, list):
            raise HTTPException(422, "INVALID_INPUT")
        bot = assistant()
        events = bot.events(message, history)
        try:
            first = next(events)  # 先驗證輸入，格式錯誤時回 422 而不是開了串流才失敗
        except InvalidInput:
            raise HTTPException(422, "INVALID_INPUT") from None

        def stream():
            try:
                yield sse(first)
                for event in events:
                    yield sse(event)
            except Exception:
                log.warning("assistant_stream_failed", extra={"event": "assistant_stream_failed"})
                yield sse({"event": "error", "data": {"code": "ASSISTANT_FAILED"}})

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/v1/health", dependencies=[Depends(authorize)])
    def health():
        report = (health_check or default_health)()
        if report["status"] != "ok":
            raise HTTPException(503, detail=report)
        return report

    return app


def default_health() -> dict:
    from assistant_rag import store
    from assistant_rag.answer import Settings
    from assistant_rag.providers import ProviderUnavailable, chat_model, embedder, reranker

    settings = Settings()
    report = {"status": "ok", "index_version": None, "method": settings.method, "embedder": settings.embedder,
              "threshold": settings.threshold, "threshold_status": settings.threshold_status, "checks": {}}
    try:
        with store.connect() as conn:
            report["index_version"] = store.active_version(conn)
        report["checks"]["database"] = "ok" if report["index_version"] else "NO_ACTIVE_INDEX"
    except Exception:
        report["checks"]["database"] = "DATABASE_UNAVAILABLE"
    try:
        if settings.embedder:
            embedder(settings.embedder).fingerprint()
        report["checks"]["embedder"] = "ok"
    except ProviderUnavailable as exc:
        report["checks"]["embedder"] = exc.code
    try:
        reranker().rerank("健康", ["健康飲食"])
        report["checks"]["reranker"] = "ok"
    except ProviderUnavailable as exc:
        report["checks"]["reranker"] = exc.code
    try:
        import httpx

        from assistant_rag.providers import CloudOpenAIChat, ollama_url
        chat = chat_model()
        report["llm_provider"] = chat.name
        if isinstance(chat, CloudOpenAIChat):
            # OpenAI 版只檢查設定，不為健康檢查花額度
            report["checks"]["llm"] = "ok" if chat.configured else "LLM_NOT_CONFIGURED"
        else:
            names = {m.get("name") for m in httpx.get(ollama_url() + "/api/tags", timeout=10).json().get("models", [])}
            report["checks"]["llm"] = "ok" if chat.name in names else "LLM_MODEL_NOT_INSTALLED"
    except Exception:
        report["checks"]["llm"] = "LLM_UNAVAILABLE"
    if any(v != "ok" for v in report["checks"].values()):
        report["status"] = "unavailable"
    return report


def create_app() -> FastAPI:
    def factory():
        from assistant_rag import store
        from assistant_rag.retrieval import Retriever
        return Assistant(Retriever(store.connect()))
    return make_app(os.environ.get("ASSISTANT_SERVICE_TOKEN", ""), factory)


def main(argv=None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m assistant_rag.server")
    parser.add_argument("--host", default=os.getenv("ASSISTANT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ASSISTANT_PORT", "8093")))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
