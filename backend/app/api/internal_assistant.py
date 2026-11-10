"""R14 飲食小幫手 OpenAI 版：團隊電腦 → 雲端內部 API，請雲端代為呼叫 OpenAI 生成回答。

團隊電腦的小幫手服務仍在本機做檢索、重排與安全檢查，只有「生成文字」這一步改由雲端呼叫 OpenAI：
- 驗證與 /internal/knowledge 相同（Cloudflare Access 服務權杖＋INTERNAL_API_TOKEN）；團隊電腦不持有 OPENAI_API_KEY。
- 每次呼叫先經 T10 額度預留最大可能費用，完成後依 usage 結算；額度不足就拒絕（不改走其他模型）。
- 呼叫鍵由團隊電腦提供（每次呼叫一個新的 call_id）；同一呼叫鍵不會預留兩次。
不記錄問題、參考資料或回答原文。
"""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.errors import ApiError
from app.api.internal_knowledge import get_session_factory, require_internal_token
from app.core.config import settings
from app.services import spend

router = APIRouter(prefix="/internal/assistant", tags=["internal"], dependencies=[Depends(require_internal_token)])

PURPOSE = "assistant_answer"
MAX_MESSAGES = 30
MAX_CONTENT = 20_000


def get_assistant_responder():
    """測試可覆寫。未設定 OPENAI_API_KEY 時為 None。"""
    from app.services.llm_client import default_responder
    return default_responder()


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_CONTENT)


class ChatBody(BaseModel):
    call_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9._\-]+$")
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    json_mode: bool = False


def max_input_tokens(messages: list[ChatMessage]) -> int:
    """預留用的輸入 token 上限：每個字元最多 2 個 token（中文約 1 個），每則訊息另加 20 個格式用量。"""
    return sum(len(m.content) * 2 + 20 for m in messages) + 50


@router.post("/chat")
def chat(body: ChatBody, factory=Depends(get_session_factory), responder=Depends(get_assistant_responder)):
    if responder is None:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "雲端未設定 OpenAI 金鑰。")
    model = settings.assistant_openai_model
    messages = [m.model_dump() for m in body.messages]
    try:
        result = spend.paid_call(
            factory, call_key=f"assistant:{body.call_id}", purpose=PURPOSE, provider="openai", model=model,
            max_input_tokens=max_input_tokens(body.messages),
            max_output_tokens=settings.assistant_openai_max_output_tokens,
            call=lambda: responder.chat_text(model=model, messages=messages, json_mode=body.json_mode,
                                             max_output_tokens=settings.assistant_openai_max_output_tokens),
        )
    except spend.BudgetExhausted:
        raise ApiError(503, "SERVICE_BUDGET_EXHAUSTED", "本月外部服務額度不足，飲食小幫手 OpenAI 版暫停使用。") from None
    except spend.CallNotBilled:
        raise ApiError(502, "LLM_UNAVAILABLE", "OpenAI 暫時無法使用（未計費），請稍後再試。") from None
    except spend.UncertainBilling:
        raise ApiError(502, "BILLING_UNCERTAIN", "OpenAI 回應不明，可能已計費；請核對額度紀錄。") from None
    if result.status != "completed" or not (result.output_text or "").strip():
        # 拒答或被截斷：不回傳不完整的內容（已計費）
        raise ApiError(502, "LLM_INCOMPLETE", "OpenAI 沒有產生完整回答。",
                       {"reason": result.incomplete_reason or result.status})
    return {"model": model, "text": result.output_text}
