"""Private team-to-cloud index embedding; every paid batch uses T10."""
import hmac

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.core.config import settings
from app.db.session import SessionLocal
from app.services import knowledge, spend

def authorize(authorization: str=Header(default='')):
    token=settings.knowledge_internal_token
    if not token:
        raise ApiError(503,'SERVICE_UNAVAILABLE','內部知識服務未啟用。')
    if not hmac.compare_digest(authorization.encode(),('Bearer '+token).encode()):
        raise ApiError(401,'UNAUTHENTICATED','內部服務驗證失敗。')

router=APIRouter(prefix='/internal/knowledge',dependencies=[Depends(authorize)])

class IndexBatch(BaseModel):
    index_version: str=Field(min_length=1,max_length=64,pattern=r'^[A-Za-z0-9._-]+$')
    batch_no: int=Field(ge=0,le=100000)
    texts: list[str]=Field(min_length=1,max_length=100)

def get_factory():
    return SessionLocal

def get_embedder():
    return knowledge.OpenAIEmbedder(settings.openai_api_key) if settings.openai_api_key else None

@router.post('/embeddings')
def embeddings(body:IndexBatch,factory=Depends(get_factory),embedder=Depends(get_embedder)):
    if embedder is None:
        raise ApiError(503,'EMBEDDING_NOT_CONFIGURED','尚未設定向量化服務。')
    try:
        values=knowledge.embed_index_batch(factory,embedder,body.index_version,body.batch_no,body.texts)
    except ValueError:
        raise ApiError(422,'INVALID_INPUT','向量化批次格式不符。') from None
    except spend.BudgetExhausted:
        raise ApiError(503,'SERVICE_BUDGET_EXHAUSTED','本月額度不足，停止向量化。') from None
    except spend.CallNotBilled:
        raise ApiError(502,'EMBEDDING_UNAVAILABLE','向量化服務暫時不可用，未計費。') from None
    except spend.UncertainBilling:
        raise ApiError(502,'BILLING_UNCERTAIN','向量化結果不明，需先核對費用。') from None
    return dict(model=embedder.model,vectors=values)
