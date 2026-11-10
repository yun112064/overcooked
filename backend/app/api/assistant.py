"""R14 飲食小幫手 API：登入使用者提問，轉送團隊電腦並串流回答（SSE）。

POST /assistant/chat  body {"message": str, "history": [{"role": "user"|"assistant", "content": str}],
                            "engine": "local"|"openai"（預設 local；openai 轉送 OpenAI 版服務）}
  成功：text/event-stream（meta → references → delta… → done；或 error），內容由團隊電腦產生、原樣轉送。
  團隊電腦不可用：503 ASSISTANT_UNAVAILABLE（details.reason）；不捏造回答。
POST /assistant/recipes  body {"answer": str, "type": str}
  依回答提到的食材從食譜庫建議 1–3 道菜（只讀業務資料庫；見 services/assistant_recipes.py）。
不記錄問題或回答原文。
"""
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, verify_origin
from app.api.errors import ApiError, forbidden, invalid_input
from app.core.config import settings
from app.db.models import Member
from app.db.session import get_db
from app.services import allergen_groups, meal_request
from app.services import assistant as service
from app.services import assistant_recipes as recipe_service
from app.services.household import is_member_of

router = APIRouter(prefix="/assistant", tags=["assistant"], dependencies=[Depends(verify_origin)])

UNAVAILABLE_MESSAGES = {
    "NOT_CONFIGURED": "飲食小幫手目前未啟用。",
    "UNREACHABLE": "暫時連不到飲食小幫手（團隊電腦可能離線），請稍後再試。",
    "TIMEOUT": "飲食小幫手回應逾時，請稍後再試。",
    "AUTH_FAILED": "飲食小幫手驗證失敗，請通知管理者。",
    "SERVICE_NOT_READY": "飲食小幫手還沒準備好（模型或資料庫未啟動），請稍後再試。",
    "SERVICE_ERROR": "飲食小幫手發生錯誤，請稍後再試。",
    "INVALID_RESPONSE": "飲食小幫手回應異常，請稍後再試。",
}

_limiter = service.RateLimiter(settings.assistant_rate_per_minute)


def get_proxy() -> service.AssistantProxy:
    """測試可覆寫此相依注入假的團隊電腦服務。"""
    return service.default_proxy()


def get_openai_proxy() -> service.AssistantProxy:
    """OpenAI 版（engine=openai）；測試可覆寫。"""
    return service.openai_proxy()


def get_limiter() -> service.RateLimiter:
    return _limiter


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=service.MAX_HISTORY_CONTENT)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=service.MAX_MESSAGE)
    history: list[Turn] = Field(default_factory=list, max_length=service.MAX_HISTORY)
    # local：地端 qwen3.5（預設）；openai：OpenAI 版服務（回答經雲端 T10 額度呼叫 OpenAI）
    engine: Literal["local", "openai"] = "local"


class RecipeBody(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)
    type: str = Field(min_length=1, max_length=40)


@router.post("/recipes")
def recipes(body: RecipeBody, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    """依回答提到的食材，從食譜庫建議 1–3 道菜；已選家庭時排除全家過敏與禁吃食材。

    只讀業務資料庫，不呼叫團隊電腦。醫療婉拒、緊急、超出範圍等回覆一律回空清單。
    """
    if body.type not in recipe_service.ANSWER_TYPES:
        return {"recipes": [], "household_filtered": False}
    household_id = auth.session.household_id
    excluded: set[str] = set()
    if household_id is not None:
        if not is_member_of(db, auth.user_id, household_id):
            raise forbidden()
        members = db.scalars(select(Member).where(Member.household_id == household_id)).all()
        try:
            excluded = meal_request.household_excluded_food_ids(members)
        except allergen_groups.AllergenGroupsInvalid:
            # 讀不到過敏原群組就不推薦，避免推薦到過敏的菜
            return {"recipes": [], "household_filtered": False}
    return {"recipes": recipe_service.suggest(db, body.answer, excluded),
            "household_filtered": household_id is not None}


@router.post("/chat")
def chat(body: ChatBody, auth: AuthContext = Depends(current_auth),
         local_proxy: service.AssistantProxy = Depends(get_proxy),
         openai_proxy: service.AssistantProxy = Depends(get_openai_proxy),
         limiter: service.RateLimiter = Depends(get_limiter)):
    proxy = openai_proxy if body.engine == "openai" else local_proxy
    if not body.message.strip():
        raise invalid_input("請輸入問題。", field="message", reason="EMPTY")
    if not limiter.allow(str(auth.user_id)):
        raise ApiError(429, "RATE_LIMITED", "提問太頻繁，請稍等一分鐘再試。")
    try:
        stream = proxy.open(body.message, [t.model_dump() for t in body.history])
    except service.AssistantRejected:
        raise invalid_input("問題格式不正確（1–500 字）。", field="message", reason="REJECTED") from None
    except service.AssistantUnavailable as exc:
        raise ApiError(503, "ASSISTANT_UNAVAILABLE", UNAVAILABLE_MESSAGES.get(exc.reason, UNAVAILABLE_MESSAGES["SERVICE_ERROR"]),
                       {"reason": exc.reason}) from None
    return StreamingResponse(stream, media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
