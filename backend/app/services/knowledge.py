"""T11 NutritionKnowledgeService（雲端端）：依最終食材向團隊電腦知識 RAG 取得審核過的知識。

流程：最終配方 FOOD 編號 →（額度預留）OpenAI 向量化查詢文字 → 團隊電腦先以食材 ID 限定、
再以 pgvector 排序 → 回傳可保存的知識快照。
- 某食材沒有知識：by_ingredient 標 not_found，可省略說明，不阻擋採用。
- 服務未設定、連不上、驗證失敗、索引不可用：status=unavailable（KNOWLEDGE_UNAVAILABLE），
  保留供重試，絕不偽裝成正常零筆。
- 額度不足（BudgetExhausted）與可能已計費（UncertainBilling）往上拋，由工作流程依 T10 規則處理。
知識不修改配方、TFDA 數值或採用資格；配方食材改變後必須重新呼叫。
"""
import copy
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.services import spend
from app.services.team_link import failure_reason, team_headers

RECORD_FIELDS = ("knowledge_id", "source_version", "ingredient_id", "excerpt", "source_url", "rights", "conditions")
# G07 出處欄位（外部來源必帶；合成資料為 None）。顯示時一律保留，不可移除（健康醫療網使用條款第 7 條）
ATTRIBUTION_FIELDS = ("source_name", "title", "published_at", "content_provider")
TAIPEI = timezone(timedelta(hours=8))


def _attribution_fields(r: dict) -> dict:
    out = {}
    for k in ATTRIBUTION_FIELDS:
        v = r.get(k)
        if v is not None and not isinstance(v, str):
            raise ValueError  # 由 _validated 轉成 INVALID_RESPONSE
        out[k] = v.strip() if isinstance(v, str) and v.strip() else None
    if out["source_name"] and not out["title"]:
        raise ValueError  # 外部來源缺標題不可顯示
    return out


def attribution(record: dict) -> str | None:
    """顯示用出處文字：來源：<網站>《標題》（發布日，台灣日期）；無來源名稱（合成資料）回傳 None。"""
    name = (record or {}).get("source_name")
    if not name:
        return None
    text = f"來源：{name}《{record.get('title') or ''}》"
    published = record.get("published_at")
    if published:
        try:
            text += f"（{datetime.fromisoformat(published).astimezone(TAIPEI).date().isoformat()}）"
        except (TypeError, ValueError):
            pass
    return text
UNAVAILABLE_CODE = "KNOWLEDGE_UNAVAILABLE"


class KnowledgeUnavailable(Exception):
    """知識服務不可用：TIMEOUT／UNREACHABLE／AUTH_FAILED／SERVICE_ERROR／MODEL_MISMATCH／INVALID_RESPONSE。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class HttpKnowledgeClient:
    """團隊電腦知識服務：POST {url}/v1/knowledge/search，Bearer token，經 HTTPS 或加密通道（T17）。"""

    def __init__(self, url: str, token: str | None, timeout: float, *, access_client_id: str | None = None,
                 access_client_secret: str | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.access_client_id, self.access_client_secret = access_client_id, access_client_secret

    def _post(self, payload: dict) -> dict:
        headers = team_headers(self.token, self.access_client_id, self.access_client_secret)
        try:
            res = httpx.post(f"{self.url}/v1/knowledge/search", json=payload, headers=headers, timeout=self.timeout)
        except httpx.TimeoutException:
            raise KnowledgeUnavailable("TIMEOUT") from None
        except httpx.HTTPError:
            raise KnowledgeUnavailable("UNREACHABLE") from None
        if res.status_code == 409:
            raise KnowledgeUnavailable("MODEL_MISMATCH")
        if (reason := failure_reason(res.status_code)) is not None:
            raise KnowledgeUnavailable(reason)
        try:
            return res.json()
        except ValueError:
            raise KnowledgeUnavailable("INVALID_RESPONSE") from None

    def search(self, food_ids: list[str], query_embedding: list[float], embedding_model: str, *,
               limit_per_ingredient: int) -> dict:
        body = self._post({"food_ids": list(food_ids), "query_embedding": query_embedding,
                           "embedding_model": embedding_model, "limit_per_ingredient": limit_per_ingredient})
        return _validated(body, set(food_ids), embedding_model)


def _validated(body, wanted: set[str], embedding_model: str) -> dict:
    """回應不可信任：錯食材、未審核或欄位缺失都視為服務異常，整份不採用。"""
    try:
        records = body["records"]
        index_version = body["index_version"]
        if not isinstance(records, list) or not isinstance(index_version, str) or not index_version:
            raise ValueError
        if body.get("embedding_model") != embedding_model:
            raise ValueError
        clean = []
        for r in records:
            if r.get("status") != "approved" or r.get("ingredient_id") not in wanted:
                raise ValueError
            if not all(isinstance(r.get(k), str) and r[k].strip() for k in RECORD_FIELDS):
                raise ValueError
            if not (isinstance(r.get("reviewer"), str) and r.get("reviewed_at")):
                raise ValueError
            clean.append({**{k: r[k] for k in RECORD_FIELDS}, "reviewer": r["reviewer"],
                          "reviewed_at": r["reviewed_at"], "distance": r.get("distance"),
                          **_attribution_fields(r)})
    except (KeyError, TypeError, ValueError, AttributeError):
        raise KnowledgeUnavailable("INVALID_RESPONSE") from None
    return {"records": clean, "index_version": index_version}


def default_knowledge_client() -> HttpKnowledgeClient | None:
    if not settings.knowledge_service_url:
        return None
    return HttpKnowledgeClient(settings.knowledge_service_url, settings.knowledge_service_token,
                               settings.knowledge_timeout_seconds, access_client_id=settings.team_access_client_id,
                               access_client_secret=settings.team_access_client_secret)


def _snapshot(food_ids: list[str], status: str, *, records=(), index_version=None, embedding_model=None,
              reason=None) -> dict:
    found = {r["ingredient_id"] for r in records}
    by_ingredient = ({} if status == "unavailable"
                     else {i: "available" if i in found else "not_found" for i in food_ids})
    snap = {"status": status, "final_food_ids": food_ids, "records": list(records), "by_ingredient": by_ingredient,
            "index_version": index_version, "embedding_model": embedding_model}
    if status == "unavailable":
        snap.update(reason=reason, code=UNAVAILABLE_CODE)
    return snap


def retrieve_knowledge(*, final_food_ids: list[str], query_text: str, client, embedder,
                       paid_call: Callable, limit_per_ingredient: int | None = None,
                       call_name: str = "embed_query") -> dict:
    """paid_call 與 JobContext.paid_call 同介面（name=...）；embedder.embed 回傳 (向量, usage)。

    同一次工作嘗試內若配方改變需重新對應，請給不同的 call_name（呼叫鍵不可重複預留）。
    """
    food_ids = sorted(set(final_food_ids))
    if not food_ids:
        return _snapshot(food_ids, "not_found")
    if client is None:
        return _snapshot(food_ids, "unavailable", reason="NOT_CONFIGURED")
    if embedder is None:
        return _snapshot(food_ids, "unavailable", reason="EMBEDDING_NOT_CONFIGURED")

    def call():
        vectors, usage = embedder.embed([query_text])
        return vectors[0], usage

    max_tokens = max(1, len(query_text.encode("utf-8")))
    try:
        vector = paid_call(name=call_name, purpose="knowledge_query_embedding", provider="openai",
                           model=embedder.model, max_input_tokens=max_tokens, max_output_tokens=0, call=call)
    except spend.CallNotBilled:
        return _snapshot(food_ids, "unavailable", embedding_model=embedder.model, reason="EMBEDDING_UNAVAILABLE")
    try:
        result = client.search(food_ids, vector, embedder.model,
                               limit_per_ingredient=limit_per_ingredient or settings.knowledge_limit_per_ingredient)
    except KnowledgeUnavailable as exc:
        return _snapshot(food_ids, "unavailable", embedding_model=embedder.model, reason=exc.reason)
    records = result["records"]
    return _snapshot(food_ids, "available" if records else "not_found", records=records,
                     index_version=result["index_version"], embedding_model=embedder.model)


def verify_citations(citations, snapshot: dict) -> dict:
    """說明只能引用本次快照實際取得、與食材相符的審核知識（沿用 T04 契約）。

    每個引用需 (knowledge_id, source_version, ingredient_id) 對上一筆取得的紀錄；
    虛構 ID、錯食材、未審核、舊版本或不可用快照都列入 invalid，不得顯示。
    """
    allowed = {(r["knowledge_id"], r["source_version"], r["ingredient_id"])
               for r in (snapshot or {}).get("records", [])}
    valid, invalid = [], []
    for citation in citations if isinstance(citations, list) else [citations]:
        key = (tuple(citation.get(k) for k in ("knowledge_id", "source_version", "ingredient_id"))
               if isinstance(citation, dict) else None)
        (valid if key in allowed else invalid).append(copy.deepcopy(citation))
    return {"valid": not invalid, "citations": valid, "invalid": invalid}


def index_embedder(factory: sessionmaker, embedder, index_version: str) -> Callable[[list[str]], list[list[float]]]:
    """建知識索引用：每批向量化各自預留並結算（呼叫鍵含索引版本與批次，重建請用新版本）。"""
    batch_no = 0

    def embed(texts: list[str]) -> list[list[float]]:
        nonlocal batch_no
        key = f"knowledge-index:{index_version}:{batch_no}"
        batch_no += 1
        return spend.paid_call(factory, call_key=key, purpose="knowledge_index_embedding", provider="openai",
                               model=embedder.model, max_input_tokens=max(1, sum(len(t.encode("utf-8")) for t in texts)),
                               max_output_tokens=0, call=lambda: embedder.embed(texts))
    return embed
