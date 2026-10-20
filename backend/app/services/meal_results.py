"""T15 結果讀取與缺值決策（R02、R05–R09、R12、R13）。

- view：把 T14 generation_results.payload 轉成結果頁需要的形狀（全家配方、缺料雙價格、數值營養、
  知識說明、昨日比對與指定重複說明、模式標示），並附後端判定的採用資格（meal_validation）。
- decide：接受缺價例外／缺必要營養繼續查看，綁定單一結果版本，冪等並檢查問卷版本。
- retry_prices：缺價重試只重讀 T02 價格快照（不呼叫外部來源、不重跑模型、不改菜），
  以新結果版本保存（原版本不變）；仍缺價需重新接受例外。
採用交易與歷史在 T16，T16 必須以 meal_validation.evaluate 重新判定，不能信任前端或本模組的快取。
"""
import copy
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import ApiError, idempotency_conflict, version_conflict
from app.db.models import GenerationResult, MealRequest, MealResultDecision
from app.meal_calc.types import IngredientRequirement
from app.services import idempotency, meal_history, meal_validation
from app.services import meal_request as meal_request_service
from app.services.generation import _jsonable, cost_snapshot
from app.services.meal_cost import meal_cost_from_db

RETRY_MISSING_PRICE = "retry_missing_price"

FALLBACK_MESSAGES = {
    "NO_MATCHING_RECIPES": "食譜資料庫沒有符合條件的組合，已自動改由 AI 生成。",
    "SPECIFIED_DISH_NOT_IN_DATABASE": "指定的菜不在食譜資料庫，已自動改由 AI 生成。",
    "SPECIFIED_DISH_DIETARY_CONFLICT": "資料庫中的指定菜含過敏或禁吃食材，已自動改由 AI 生成（仍避開這些食材）。",
    "ALL_EXCLUDED_BY_PREVIOUS_DAY": "排除昨日已採用的菜後資料庫沒有可行組合，已自動改由 AI 生成（仍避開昨日菜）。",
}


CONFLICT_MESSAGE = "這個送出識別碼已用於不同的決定，請重新操作。"


def _hash(kind: str, result: GenerationResult, request_version: int) -> str:
    return idempotency.request_hash(kind, result.id, request_version)


def decisions_for(db: Session, result: GenerationResult) -> list[MealResultDecision]:
    return db.scalars(select(MealResultDecision).where(MealResultDecision.generation_result_id == result.id)
                      .order_by(MealResultDecision.created_at)).all()


def decision_records(rows: list[MealResultDecision]) -> list[dict]:
    """evaluate 的輸入：只含接受／繼續兩種決定（重試不是例外接受）。"""
    return [{"kind": r.kind, **(r.details or {})} for r in rows if r.kind in meal_validation.DECISION_KINDS]


def eligibility(db: Session, request: MealRequest, result: GenerationResult) -> dict:
    """後端唯一的採用資格判定（T16 採用時應以相同方式重算）。"""
    rows = decisions_for(db, result)
    out = meal_validation.evaluate(result.payload, decisions=decision_records(rows),
                                   current_request_version=request.version)
    out["decisions"] = [public_decision(db, r) for r in rows]
    return out


def public_decision(db: Session, row: MealResultDecision) -> dict:
    produced = db.get(GenerationResult, row.produced_result_id) if row.produced_result_id else None
    return {"id": str(row.id), "kind": row.kind, "result_version": row.result_version,
            "request_version": row.request_version, "missing_food_ids": (row.details or {}).get("missing_food_ids"),
            "details": row.details, "produced_result_version": produced.result_version if produced else None,
            "created_at": row.created_at.isoformat()}


def _ingredient(r: dict) -> dict:
    """整餐用量；display_* 為畫面取整（克到 1 g、容量單位到 0.5，無換算照原單位／原文）。"""
    return {"food_id": r["food_id"], "name": r["name"], "weight_basis": r.get("weight_basis"),
            "weight_basis_source": r.get("weight_basis_source"),
            "whole_meal_grams": r.get("whole_meal_grams"), "display_grams": r.get("display_grams"),
            "display_amount": r.get("display_amount", r.get("display_grams")),
            "display_unit": r.get("display_unit", "g" if r.get("display_grams") is not None else None),
            "display_text": r.get("display_text")}


def _dishes(payload: dict) -> list[dict]:
    rows = (payload.get("calculation") or {}).get("ingredients") or []
    out = []
    for d in payload["dishes"]:
        key = d.get("recipe_id") or d["standard_dish_id"]
        out.append({
            "standard_dish_id": d["standard_dish_id"], "dish_name": d["dish_name"], "aliases": d.get("aliases", []),
            "source": d["source"], "recipe_id": d.get("recipe_id"), "specified": d.get("specified", False),
            "cooking_method": d.get("cooking_method"), "steps": d.get("steps", []),
            "ingredients": [_ingredient(r) for r in rows if r.get("dish_id") == key],
        })
    return out


def _nutrition(payload: dict) -> dict:
    calc = payload.get("calculation") or {}
    final = calc.get("final") or {}
    return {"total_n": calc.get("total_n"), "target": final.get("target"), "energy_kcal": final.get("energy_kcal"),
            "protein_g": final.get("protein_g"), "gap": final.get("gap") or {},
            "within_range": final.get("within_range", False), "other_nutrients": final.get("other_nutrients") or {},
            "missing_nutrients": final.get("missing_nutrients") or {},
            "baseline_adjusted": (calc.get("baseline") or {}).get("adjusted", False),
            # P2：用量是否已通過驗證；不可採用時的具體調整建議
            "quantities_verified": calc.get("quantities_verified", calc.get("adoptable", False)),
            "suggestions": calc.get("suggestions") or [],
            "unresolved_ingredients": calc.get("unresolved_ingredients") or []}


QUOTE_FIELDS = ("retailer", "source", "source_url", "product_name", "product_url", "price", "package_quantity",
                "package_unit", "package_description", "queried_at", "valid_until", "region_condition",
                "store_condition", "promotion_description")


def _prices(payload: dict) -> dict:
    cost = payload.get("cost") or {}
    if cost.get("status") != "computed":
        return {"status": cost.get("status", "not_computed"), "reason": cost.get("reason"), "budget": None,
                "budget_status": None, "price_complete": False, "purchase_total": None, "cost_total": None,
                "known_purchase_total": None, "known_cost_total": None, "items": []}
    names = {r["food_id"]: r["name"] for r in (payload.get("calculation") or {}).get("ingredients") or []}
    items = []
    for ln in cost["lines"]:
        if ln["price_status"] not in ("known", "unknown"):
            continue  # 已有種類與免計價（水）不列入價格區
        quote = ln.get("quote") or {}
        items.append({"food_id": ln["food_id"], "name": names.get(ln["food_id"], ln["food_id"]),
                      "shortage_grams": ln["shortage_grams"], "price_status": ln["price_status"],
                      "packs": ln.get("packs"), "purchase_price": ln.get("purchase_price"), "cost": ln.get("cost"),
                      "missing_reason": ln.get("missing_reason"), **{k: quote.get(k) for k in QUOTE_FIELDS}})
    return {"status": "computed", **{k: cost.get(k) for k in (
        "budget", "budget_status", "price_complete", "purchase_total", "cost_total", "known_purchase_total",
        "known_cost_total")}, "items": items}


def _previous_day(payload: dict) -> dict:
    ctx = payload.get("previous_day") or {}
    exceptions = set(ctx.get("specified_exceptions") or [])
    repeats = [d["dish_name"] for d in payload["dishes"] if exceptions & meal_history.dish_keys(d)]
    return {"meal_date": ctx.get("meal_date"), "previous_date": ctx.get("previous_date"),
            "has_records": bool(ctx.get("records")), "records": ctx.get("records") or [],
            "excluded_dishes": ctx.get("excluded_dishes") or [], "specified_repeats": repeats}


def view(db: Session, request: MealRequest, result: GenerationResult) -> dict:
    p = result.payload
    derived = db.get(GenerationResult, result.derived_from_result_id) if result.derived_from_result_id else None
    return {
        "meal_request_id": str(request.id), "request_status": request.status,
        "current_request_version": request.version,
        "result_version": result.result_version, "request_version": result.request_version,
        "derived_from_result_version": derived.result_version if derived else None,
        "created_at": result.created_at.isoformat(),
        "mode": result.mode, "mode_label": p["mode_label"], "fallback_reason": result.fallback_reason,
        "fallback_message": FALLBACK_MESSAGES.get(result.fallback_reason, "食譜資料庫沒有可行組合，已自動改由 AI 生成。")
        if result.fallback_reason else None,
        "dishes": _dishes(p), "nutrition": _nutrition(p), "prices": _prices(p),
        "knowledge": p.get("explanation") or {"status": "not_found", "items": []},
        "previous_day": _previous_day(p),
        "eligibility": eligibility(db, request, result),
    }


def summaries(db: Session, request: MealRequest) -> list[dict]:
    rows = db.scalars(select(GenerationResult).where(GenerationResult.meal_request_id == request.id)
                      .order_by(GenerationResult.result_version.desc())).all()
    return [{"result_version": r.result_version, "request_version": r.request_version, "mode": r.mode,
             "mode_label": r.payload["mode_label"], "created_at": r.created_at.isoformat(),
             "adoptable": eligibility(db, request, r)["adoptable"]} for r in rows]


def _latest_version(db: Session, request: MealRequest) -> int:
    return db.scalar(select(func.max(GenerationResult.result_version))
                     .where(GenerationResult.meal_request_id == request.id)) or 0


def _refresh_status(db: Session, request: MealRequest, result: GenerationResult) -> None:
    """只有最新結果版本、且問卷仍停在結果狀態時才更新（不覆蓋草稿、生成中或已採用）。"""
    if request.status in ("result_ready", "awaiting_decision") and result.result_version == _latest_version(db, request):
        request.status = meal_validation.request_status(result.payload,
                                                        decisions=decision_records(decisions_for(db, result)))
        request.updated_at = datetime.now(timezone.utc)


def _existing(db: Session, household_id, key: str, digest: str) -> MealResultDecision | None:
    return idempotency.find_by_key(db, MealResultDecision, household_id, key, digest, message=CONFLICT_MESSAGE)


def _check_versions(request: MealRequest, result: GenerationResult, request_version: int) -> None:
    if request.status in ("queued", "running"):
        raise ApiError(409, "VERSION_CONFLICT", "此問卷正在生成中，請等待結果。", {"reason": "GENERATION_IN_PROGRESS"})
    if request_version != request.version or result.request_version != request.version:
        raise version_conflict()


def _not_applicable(evaluation: dict) -> ApiError:
    return ApiError(422, "INVALID_INPUT", "此結果目前不需要（或不允許）這個決定。",
                    {"reason": "DECISION_NOT_APPLICABLE", "blocks": [b["code"] for b in evaluation["blocks"]]})


def _insert(db: Session, row: MealResultDecision, household_id, key: str) -> tuple[MealResultDecision, bool]:
    db.add(row)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raced = db.scalar(select(MealResultDecision).where(MealResultDecision.household_id == household_id,
                                                           MealResultDecision.idempotency_key == key))
        if raced is None:
            raced = db.scalar(select(MealResultDecision).where(
                MealResultDecision.generation_result_id == row.generation_result_id,
                MealResultDecision.kind == row.kind))
        if raced is None or (raced.idempotency_key == key and raced.request_hash != row.request_hash):
            raise idempotency_conflict(CONFLICT_MESSAGE) from None
        return raced, False
    return row, True


def decide(db: Session, *, household_id, request: MealRequest, result: GenerationResult, kind: str,
           request_version: int, idempotency_key: str, user_id) -> tuple[MealResultDecision, bool]:
    """記錄使用者決定；呼叫端負責 commit。同鍵同命令回傳同一筆，同一結果同一種決定只有一筆。"""
    digest = _hash(kind, result, request_version)
    existing = _existing(db, household_id, idempotency_key, digest)
    if existing is not None:
        return existing, False
    _check_versions(request, result, request_version)
    rows = decisions_for(db, result)
    same = next((r for r in rows if r.kind == kind), None)
    if same is not None:
        return same, False
    evaluation = meal_validation.evaluate(result.payload, decisions=decision_records(rows),
                                          current_request_version=request.version)
    pending = next((d for d in evaluation["pending_decisions"] if d["kind"] == kind), None)
    if pending is None:
        raise _not_applicable(evaluation)
    details = {}
    if kind == meal_validation.ACCEPT_MISSING_PRICE:
        cost = result.payload["cost"]
        details = {"missing_food_ids": pending["missing_food_ids"], "known_cost_total": cost.get("known_cost_total"),
                   "known_purchase_total": cost.get("known_purchase_total"), "budget": cost.get("budget")}
    row, created = _insert(db, MealResultDecision(
        household_id=household_id, meal_request_id=request.id, generation_result_id=result.id,
        result_version=result.result_version, request_version=request_version, kind=kind,
        idempotency_key=idempotency_key, request_hash=digest, details=details, created_by=user_id),
        household_id, idempotency_key)
    if created:
        _refresh_status(db, request, result)
    return row, created


def retry_prices(db: Session, *, household_id, request: MealRequest, result: GenerationResult, request_version: int,
                 idempotency_key: str, user_id, now: datetime | None = None) -> tuple[MealResultDecision, bool]:
    """缺價重試：重讀最新價格快照，產生新結果版本（呼叫端已鎖問卷列、負責 commit）。"""
    digest = _hash(RETRY_MISSING_PRICE, result, request_version)
    existing = _existing(db, household_id, idempotency_key, digest)
    if existing is not None:
        return existing, False
    _check_versions(request, result, request_version)
    same = next((r for r in decisions_for(db, result) if r.kind == RETRY_MISSING_PRICE), None)
    if same is not None:
        return same, False
    cost = result.payload.get("cost") or {}
    if cost.get("status") != "computed" or cost.get("budget_status") != "PRICE_INCOMPLETE":
        raise _not_applicable(meal_validation.evaluate(result.payload, decisions=[],
                                                       current_request_version=request.version))
    draft = meal_request_service.load(request.payload)
    requirements = [IngredientRequirement(food_id=r["food_id"], grams=Decimal(r["grams"]))
                    for r in result.payload["calculation"]["requirements"]]
    now = now or datetime.now(timezone.utc)
    fresh = _jsonable(cost_snapshot(meal_cost_from_db(db, requirements, draft.available_ingredient_ids,
                                                      draft.budget, now=now)))
    version = _latest_version(db, request) + 1
    payload = copy.deepcopy(result.payload)
    payload["cost"] = fresh
    payload["validation"] = {**payload.get("validation", {}), "budget_status": fresh["budget_status"]}
    payload["result_version"] = version
    payload["price_retry"] = {"from_result_version": result.result_version, "retried_at": now.isoformat(),
                              "price_source": "latest_t02_snapshots"}
    new = GenerationResult(household_id=household_id, meal_request_id=request.id,
                           request_version=result.request_version, job_id=None, derived_from_result_id=result.id,
                           result_version=version, mode=result.mode, fallback_reason=result.fallback_reason,
                           payload=payload)
    db.add(new)
    db.flush()
    row, created = _insert(db, MealResultDecision(
        household_id=household_id, meal_request_id=request.id, generation_result_id=result.id,
        result_version=result.result_version, request_version=request_version, kind=RETRY_MISSING_PRICE,
        idempotency_key=idempotency_key, request_hash=digest,
        details={"produced_result_version": version, "budget_status": fresh["budget_status"]},
        produced_result_id=new.id, created_by=user_id), household_id, idempotency_key)
    if not created:
        db.delete(new)  # 並行重送已由另一請求產生新版本
        return row, False
    _refresh_status(db, request, new)
    return row, True
