"""T15 結果重試工作：以既有結果版本為基礎產生新版本，原版本不變。

- knowledge_retry（pre-review #3「保留供重試」）：只對同一結果的最終食材重新檢索審核知識並產生說明；
  菜單、份量、營養計算與價格原樣沿用，付費呼叫（向量化、說明）經 T10 額度預留／結算。
- price_retry（P3）：對缺價食材排入有速率限制的 T02 定向查價（PX Mart，請求間隔
  price_retry_delay_seconds、每次最多 price_retry_max_foods 種），查完以最新價格快照重算成本。
  查價中＝工作 queued／running；查價失敗＝工作 failed（PRICE_LOOKUP_FAILED），都可在結果頁辨識。

新版本的 job_id 為重試工作、derived_from_result_id 指向來源版本；新版本沒有沿用來源版本的使用者決定
（例如缺價例外需在新版本重新接受）。問卷狀態只在新版本是最新版本、且問卷仍停在結果狀態時更新。
"""
import copy
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import GenerationResult, MealRequest, MealResultDecision
from app.meal_calc.types import IngredientRequirement
from app.services import jobs, meal_validation
from app.services import meal_request as meal_request_service
from app.services.generation import (_DEFAULT, FoodDictionary, _jsonable, cost_snapshot, explanation_evidence,
                                     knowledge_section, next_result_version)
from app.services.meal_cost import meal_cost_from_db

RETRY_MISSING_PRICE = "retry_missing_price"


class PriceLookupBusy(Exception):
    """另一個查價執行正持有 T02 鎖（例如每日排程）：稍後自動重試。"""


def _source(ctx) -> dict:
    with ctx.factory() as db:
        row = db.get(GenerationResult, uuid.UUID(ctx.job.params["source_result_id"]))
        if row is None:
            raise jobs.JobFailure("RESULT_NOT_FOUND", "找不到要重試的結果版本。")
        return copy.deepcopy(row.payload)


def _paid_call(ctx, call_keys: list[str]):
    def paid_call(**kwargs):
        call_keys.append(f"{ctx.job.id}:{ctx.job.attempts}:{kwargs['name']}")
        return ctx.paid_call(**kwargs)
    return paid_call


def final_food_ids(payload: dict) -> list[str]:
    """結果的最終菜色食材（不含主食）：與生成時查知識的集合相同。"""
    return sorted({f for d in payload["dishes"] for f in d.get("food_ids", [])})


# --- 只重試知識說明 ---

def make_knowledge_retry_handler(*, responder=_DEFAULT, knowledge_client=_DEFAULT, embedder=_DEFAULT, now=None):
    from app.services import knowledge
    from app.services.llm_client import default_embedder, default_responder

    def handler(ctx) -> dict:
        payload = _source(ctx)
        with ctx.factory() as db:
            names = FoodDictionary.load(db).names
        call_keys: list[str] = []
        snapshot, explanation = knowledge_section(
            final_food_ids=final_food_ids(payload), names=names, paid_call=_paid_call(ctx, call_keys),
            responder=default_responder() if responder is _DEFAULT else responder,
            knowledge_client=knowledge.default_knowledge_client() if knowledge_client is _DEFAULT else knowledge_client,
            embedder=default_embedder() if embedder is _DEFAULT else embedder,
            stage=ctx.stage, factory=ctx.factory)
        payload["knowledge"], payload["explanation"] = snapshot, explanation
        payload["evidence"] = {**payload.get("evidence", {}), **explanation_evidence(snapshot, explanation),
                               "knowledge_retry_call_keys": call_keys}
        payload["knowledge_retry"] = {"from_result_version": ctx.job.params["source_result_version"],
                                      "retried_at": (now or datetime.now(timezone.utc)).isoformat(),
                                      "job_id": str(ctx.job.id)}
        return _jsonable(payload)
    handler.persist = persist_derived_result
    return handler


# --- 缺價定向查價 ---

def default_price_lookup(food_ids: list[str]) -> dict:
    """以 T02 PriceCollectionService 只查指定 FOOD 編號（PX Mart、低頻率），結果寫入價格快照。"""
    from app.core.config import settings
    from app.pricing_scheduler import SchedulerSettings
    from app.source_adapters.price_collection import PriceCollectionService
    from app.source_adapters.pricing_repository import PricingRepository
    from app.source_adapters.pxmart import PxMartAdapter

    config = SchedulerSettings.from_env()
    service = PriceCollectionService(
        workbook_path=config.workbook_path, repository=PricingRepository(),
        adapter_factory=lambda: PxMartAdapter(timeout=config.timeout_seconds, retries=config.retries),
        delay_seconds=max(settings.price_retry_delay_seconds, config.delay_seconds))
    now = datetime.now(timezone.utc)
    result = service.run(trigger_type="targeted", scheduled_for=now, run_key=f"pxmart-targeted-{uuid.uuid4()}",
                         food_ids=set(food_ids))
    if result.status == "skipped":
        raise PriceLookupBusy(result.skipped_reason or "busy")
    return {"status": result.status, "auto_adopted": result.auto_adopted_count, "estimates": result.estimate_count,
            "missing": result.missing_count}


def make_price_retry_handler(*, price_lookup=None, now=None):
    def handler(ctx) -> dict:
        from app.core.config import settings
        payload = _source(ctx)
        missing = meal_validation.missing_price_food_ids(payload)[:settings.price_retry_max_foods]
        ctx.stage("price_lookup")
        try:
            outcome = (price_lookup or default_price_lookup)(missing)
        except PriceLookupBusy:
            raise jobs.RetryableJobError("PRICE_LOOKUP_BUSY", code="PRICE_LOOKUP_BUSY",
                                         user_message="價格來源正在進行其他查價，稍後會自動再試一次。") from None
        except Exception:  # noqa: BLE001 — 外部來源失敗：可辨識的失敗狀態，原結果不變
            raise jobs.JobFailure("PRICE_LOOKUP_FAILED", "缺價食材的定向查價失敗；原結果不變，可稍後再試或接受缺價例外。")
        ctx.stage("calculating")
        draft = meal_request_service.load(ctx.input_payload())
        requirements = [IngredientRequirement(food_id=r["food_id"], grams=Decimal(r["grams"]))
                        for r in payload["calculation"]["requirements"]]
        moment = now or datetime.now(timezone.utc)
        with ctx.factory() as db:
            fresh = cost_snapshot(meal_cost_from_db(db, requirements, draft.available_ingredient_ids, draft.budget,
                                                    now=moment))
        fresh["unquantified_food_ids"] = payload.get("cost", {}).get("unquantified_food_ids", [])
        payload["cost"] = fresh
        payload["validation"] = {**payload.get("validation", {}), "budget_status": fresh["budget_status"]}
        payload["price_retry"] = {"from_result_version": ctx.job.params["source_result_version"],
                                  "retried_at": moment.isoformat(), "price_source": "t02_targeted_lookup",
                                  "food_ids": missing, "lookup": outcome, "job_id": str(ctx.job.id)}
        return _jsonable(payload)
    handler.persist = persist_price_retry
    return handler


# --- 寫入新版本（jobs.complete 的交易內） ---

def persist_derived_result(db, job, result: dict) -> tuple[dict, int]:
    source_id = uuid.UUID(job.params["source_result_id"])
    source = db.get(GenerationResult, source_id)
    version = next_result_version(db, job.meal_request_id)
    row = GenerationResult(household_id=job.household_id, meal_request_id=job.meal_request_id,
                           request_version=source.request_version, job_id=job.id, derived_from_result_id=source_id,
                           result_version=version, mode=source.mode, fallback_reason=source.fallback_reason,
                           payload={**result, "result_version": version})
    db.add(row)
    db.flush()
    status = meal_validation.request_status(row.payload, decisions=[])
    request = db.get(MealRequest, job.meal_request_id)
    if request.status in ("result_ready", "awaiting_decision"):
        request.status = status
        request.updated_at = datetime.now(timezone.utc)
    summary = {"generation_result_id": str(row.id), "result_version": version, "mode": row.mode,
               "mode_label": result["mode_label"], "fallback_reason": row.fallback_reason,
               "derived_from_result_version": source.result_version, "request_status": status}
    return summary, version


def persist_price_retry(db, job, result: dict) -> tuple[dict, int]:
    """新版本＋來源版本的 retry_missing_price 紀錄（指向新版本；已有紀錄時不重複建立）。"""
    summary, version = persist_derived_result(db, job, result)
    source_id = uuid.UUID(job.params["source_result_id"])
    exists = db.scalar(select(MealResultDecision.id).where(MealResultDecision.generation_result_id == source_id,
                                                           MealResultDecision.kind == RETRY_MISSING_PRICE))
    if exists is None:
        source = db.get(GenerationResult, source_id)
        db.add(MealResultDecision(
            household_id=job.household_id, meal_request_id=job.meal_request_id, generation_result_id=source_id,
            result_version=source.result_version, request_version=job.input_version, kind=RETRY_MISSING_PRICE,
            idempotency_key=job.idempotency_key, request_hash=job.request_hash,
            details={"produced_result_version": version, "budget_status": result["cost"]["budget_status"],
                     "job_id": str(job.id)},
            produced_result_id=uuid.UUID(summary["generation_result_id"])))
    return summary, version
