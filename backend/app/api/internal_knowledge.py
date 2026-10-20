"""團隊電腦 → 雲端的內部 API（pre-review #6）：補抓佇列與建索引向量化，團隊電腦不需要雲端 DATABASE_URL。

驗證與 T17 相同的兩層：
1. Cloudflare Access：只對內部 hostname（例如 cloud-internal.<網域>，cloudflared 直接轉到 backend:8000）
   建 Access 應用程式、Policy 只允許團隊電腦的服務權杖（CF-Access-Client-Id／Secret）。
2. 服務 Bearer token：Authorization: Bearer <INTERNAL_API_TOKEN>，以常數時間比對；未設定時整組 API 關閉（503）。
公開網站的 Caddy 另外擋掉 /api/internal/*，一般使用者的瀏覽器路徑到不了這裡。

向量化在雲端經 T10 額度預留／結算後呼叫 OpenAI（decisions #2）；團隊電腦不持有 OPENAI_API_KEY。
"""
import hmac

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.core.config import settings
from app.db.session import SessionLocal
from app.services import knowledge, knowledge_queue, spend


def require_internal_token(authorization: str = Header(default="")) -> None:
    token = settings.internal_api_token
    if not token:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "內部 API 未啟用。")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
        raise ApiError(401, "UNAUTHENTICATED", "內部 API 驗證失敗。")


router = APIRouter(prefix="/internal/knowledge", tags=["internal"], dependencies=[Depends(require_internal_token)])


def get_session_factory():
    """短交易用的 sessionmaker（佇列認領、額度預留各自提交）；測試可覆寫。"""
    return SessionLocal


def get_index_embedder():
    from app.services.llm_client import default_embedder
    return default_embedder()


class ClaimBody(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class CompleteBody(BaseModel):
    outcomes: dict[str, dict[str, int]] = Field(max_length=200)


class EmbedBody(BaseModel):
    index_version: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9._\-]+$")
    batch_no: int = Field(ge=0, le=100_000)
    texts: list[str] = Field(min_length=1, max_length=100)


@router.get("/crawl-requests/summary")
def crawl_summary(factory=Depends(get_session_factory)):
    return {"pending": knowledge_queue.pending_count(factory)}


@router.post("/crawl-requests/claim")
def claim_crawl_requests(body: ClaimBody, factory=Depends(get_session_factory)):
    """認領待補食材（需求次數多者優先）；逾時未回報的認領可再被認領。只回傳 FOOD 編號與次數。"""
    rows = knowledge_queue.claim(factory, limit=body.limit)
    return {"requests": [{"food_id": r["food_id"], "request_count": r["request_count"]} for r in rows]}


@router.post("/crawl-requests/complete")
def complete_crawl_requests(body: CompleteBody, factory=Depends(get_session_factory)):
    outcomes = {k: v for k, v in body.outcomes.items() if knowledge_queue.FOOD_ID.match(k)}
    return {"updated": knowledge_queue.mark_crawled(factory, outcomes)}


@router.post("/embeddings")
def embed_index_batch(body: EmbedBody, factory=Depends(get_session_factory), embedder=Depends(get_index_embedder)):
    """建索引的一批向量化：雲端經 T10 額度預留後呼叫 OpenAI，回傳向量。呼叫鍵含索引版本與批次（重建請用新版本）。"""
    if embedder is None:
        raise ApiError(503, "EMBEDDING_NOT_CONFIGURED", "雲端未設定向量化服務。")
    if any(len(t) > 8000 for t in body.texts):
        raise ApiError(422, "INVALID_INPUT", "單段文字過長。", {"reason": "TEXT_TOO_LONG"})
    try:
        vectors = knowledge.embed_index_batch(factory, embedder, body.index_version, body.batch_no, body.texts)
    except spend.BudgetExhausted:
        raise ApiError(503, "SERVICE_BUDGET_EXHAUSTED", "本月外部服務額度不足，已停止建索引。") from None
    except spend.CallNotBilled:
        raise ApiError(502, "EMBEDDING_UNAVAILABLE", "向量化服務暫時無法使用（未計費），請以新索引版本重試。") from None
    except spend.UncertainBilling:
        raise ApiError(502, "BILLING_UNCERTAIN", "向量化結果不明，可能已計費；請核對額度紀錄後以新索引版本重建。") from None
    return {"model": embedder.model, "vectors": vectors}
