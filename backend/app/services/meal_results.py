"""T15 結果讀取與缺值決策（R02、R05–R09、R12、R13）。

- view：把 T14 generation_results.payload 轉成結果頁需要的形狀（全家配方、缺料雙價格、數值營養、
  知識說明、昨日比對與指定重複說明、模式標示），並附後端判定的採用資格（meal_validation）。
- decide：接受缺價例外／缺必要營養繼續查看，綁定單一結果版本，冪等並檢查問卷版本。
- request_price_retry：缺價食材排入 T02 定向查價背景工作（有速率限制），完成後以新結果版本保存
  （原版本不變、不重跑模型、不改菜）；仍缺價需重新接受例外。查價中／失敗可由 retries 辨識。
- request_knowledge_retry：知識服務故障或額度不足時，只重試知識說明（背景工作），產生新版本。
採用交易與歷史在 T16，T16 必須以 meal_validation.evaluate 重新判定，不能信任前端或本模組的快取。
"""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import ApiError, idempotency_conflict, version_conflict
from app.db.models import GenerationResult, MealRequest, MealResultDecision
from app.services import idempotency, jobs, meal_history, meal_validation


FALLBACK_MESSAGES = {
    "NO_MATCHING_RECIPES": "食譜資料庫沒有符合條件的組合，已自動改由 AI 生成。",
    "SPECIFIED_DISH_NOT_IN_DATABASE": "指定的菜不在食譜資料庫，已自動改由 AI 生成。",
    "DATABASE_MENU_NUTRITION_INFEASIBLE": "資料庫的候選菜單都無法在可調範圍內符合熱量與蛋白質目標，已自動改由 AI 生成。",
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
            "display_text": r.get("display_text"),
            # decisions #19：克數或烹調係數來自 LLM 推估時標示；estimates 保留模型、版本與覆核狀態供追溯
            "llm_estimated": bool(r.get("llm_estimated")),
            "estimate_label": LLM_ESTIMATE_LABEL if r.get("llm_estimated") else None,
            "estimated_nutrients": r.get("estimated_nutrients") or [],
            "estimates": r.get("estimates") or [],
            # decisions #28：TFDA 樣本對照來源；LLM 推薦的對照標示「LLM 對照」
            "tfda_mapping_source": r.get("tfda_mapping_source"), "tfda_sample_id": r.get("tfda_sample_id"),
            "llm_mapped": bool(r.get("llm_mapped")),
            "mapping_label": LLM_MAPPING_LABEL if r.get("llm_mapped") else None,
            # decisions #40：保留率視為 1 的非必要營養素（畫面標示「近似」）
            "approximate_nutrients": r.get("approximate_nutrients") or []}


LLM_ESTIMATE_LABEL = "LLM 推估"
LLM_MAPPING_LABEL = "LLM 對照"
APPROXIMATION_LABEL = "近似"


def _approximations(calc: dict) -> dict:
    summary = calc.get("approximations") or {}
    used = bool(summary.get("used"))
    return {"used": used, "food_ids": summary.get("food_ids") or [], "nutrients": summary.get("nutrients") or [],
            "basis": summary.get("basis"), "label": APPROXIMATION_LABEL if used else None}


def _llm_mappings(calc: dict) -> dict:
    summary = calc.get("llm_mappings") or {}
    return {"used": bool(summary.get("used")), "food_ids": summary.get("food_ids") or [],
            "label": LLM_MAPPING_LABEL if summary.get("used") else None}


def _llm_estimates(calc: dict) -> dict:
    summary = calc.get("llm_estimates") or {}
    return {"used": bool(summary.get("used")), "food_ids": summary.get("food_ids") or [],
            "nutrients": summary.get("nutrients") or [],
            "label": LLM_ESTIMATE_LABEL if summary.get("used") else None}


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


def staple_view(payload: dict) -> dict | None:
    """主食（decisions #16）：名稱、TFDA 食材、整餐用量；計入營養、不計價格。設定待營養審核者確認。"""
    staple = payload.get("staple")
    if not staple:
        return None
    rows = [r for r in (payload.get("calculation") or {}).get("ingredients") or [] if r.get("role") == "staple"]
    row = _ingredient(rows[0]) if rows else {}
    return {"label": staple["label"], "tfda_name": staple.get("tfda_name"), "food_id": staple.get("food_id"),
            "review_status": staple.get("review_status"), "unresolved_reason": staple.get("unresolved_reason"),
            "whole_meal_grams": row.get("whole_meal_grams"), "display_amount": row.get("display_amount"),
            "display_unit": row.get("display_unit"), "display_text": row.get("display_text"),
            "counts_for_price": False, "llm_estimated": bool(row.get("llm_estimated"))}


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
            "unresolved_ingredients": calc.get("unresolved_ingredients") or [],
            "llm_estimates": _llm_estimates(calc), "llm_mappings": _llm_mappings(calc),
            "approximations": _approximations(calc)}


QUOTE_FIELDS = ("retailer", "source", "source_url", "product_name", "product_url", "price", "package_quantity",
                "package_unit", "package_description", "queried_at", "valid_until", "region_condition",
                "store_condition", "promotion_description")


# 畫面標示的價格來源名稱（決策 #25）
SOURCE_NAMES = {"PX Mart": "全聯", "萬家福": "萬家福"}


def _source_name(quote: dict) -> str | None:
    retailer = quote.get("retailer")
    return SOURCE_NAMES.get(retailer, retailer) if retailer else None


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
                      "missing_reason": ln.get("missing_reason"), "source_name": _source_name(quote),
                      **{k: quote.get(k) for k in QUOTE_FIELDS}})
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
        "dishes": _dishes(p), "staple": staple_view(p), "nutrition": _nutrition(p), "prices": _prices(p),
        "knowledge": {**(p.get("explanation") or {"status": "not_found", "items": []}),
                      "retry_available": knowledge_retry_available(p)},
        "knowledge_retry": p.get("knowledge_retry"), "price_retry": p.get("price_retry"),
        "retries": retries(db, result),
        "previous_day": _previous_day(p),
        "previous_day_check": previous_day_check(db, p),
        "eligibility": eligibility(db, request, result),
    }


def previous_day_check(db: Session, payload: dict) -> dict:
    """pre-review #4：以目前的前一日已採用紀錄重新比對（與 T16 採用交易同一函式）。

    衝突時前端停用採用並說明；採用交易內仍會再驗一次（這裡只是預先提示，不能取代交易內重驗）。
    """
    check = meal_history.recheck_previous_day(db, payload["previous_day"], payload["dishes"])
    out = {"status": check["status"], "history_changed": bool(check.get("history_changed"))}
    if check["status"] == "conflict":
        out.update(code=check["code"], conflicting_dishes=[d["dish_name"] for d in check["conflicting_dishes"]],
                   previous_date=check["context"]["previous_date"],
                   message="生成後前一日的已採用紀錄有變動，此菜單與昨日菜重複，不能採用；請重新生成。")
    return out


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


def _retry_state(job) -> dict | None:
    """重試工作狀態：queued／running＝進行中（查價中／說明產生中），failed＝失敗（附原因），succeeded＝新版本。"""
    if job is None:
        return None
    return {"job_id": str(job.id), "status": job.status, "stage": job.stage, "attempts": job.attempts,
            "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None,
            "produced_result_version": job.result_version if job.status == "succeeded" else None,
            "created_at": job.created_at.isoformat()}


def retries(db: Session, result: GenerationResult) -> dict:
    return {"price": _retry_state(jobs.latest_retry(db, result.id, jobs.PRICE_RETRY)),
            "knowledge": _retry_state(jobs.latest_retry(db, result.id, jobs.KNOWLEDGE_RETRY))}


def knowledge_retry_available(payload: dict) -> bool:
    """知識服務故障或額度不足（KNOWLEDGE_UNAVAILABLE）時才可「只重試知識說明」；缺知識（not_found）不需要。"""
    return (payload.get("explanation") or {}).get("status") == "unavailable"


def request_knowledge_retry(db: Session, *, household_id, request: MealRequest, result: GenerationResult,
                            request_version: int, idempotency_key: str):
    """pre-review #3：同一結果版本只重新檢索知識並產生說明（背景工作、經 T10 額度），產生新版本。"""
    if not knowledge_retry_available(result.payload):
        raise ApiError(422, "INVALID_INPUT", "此結果的知識說明不需要重試。", {"reason": "RETRY_NOT_APPLICABLE"})
    return jobs.submit_result_retry(db, household_id=household_id, meal_request=request, source=result,
                                    kind=jobs.KNOWLEDGE_RETRY, request_version=request_version,
                                    idempotency_key=idempotency_key, paid=True)


def request_price_retry(db: Session, *, household_id, request: MealRequest, result: GenerationResult,
                        request_version: int, idempotency_key: str):
    """P3：缺價食材排入 T02 定向查價背景工作；完成後以新價格產生新版本（菜單與營養不變）。"""
    cost = result.payload.get("cost") or {}
    if cost.get("status") != "computed" or cost.get("budget_status") != "PRICE_INCOMPLETE":
        raise _not_applicable(meal_validation.evaluate(result.payload, decisions=[],
                                                       current_request_version=request.version))
    missing = meal_validation.missing_price_food_ids(result.payload)
    return jobs.submit_result_retry(db, household_id=household_id, meal_request=request, source=result,
                                    kind=jobs.PRICE_RETRY, request_version=request_version,
                                    idempotency_key=idempotency_key, params={"food_ids": missing}, paid=False)
