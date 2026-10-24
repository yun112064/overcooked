"""T16 採用交易與已採用餐食紀錄（R09、R10、R13；MealPlanSnapshot 契約）。

adopt 在呼叫端開的單一資料庫交易內完成全部步驟，任一步失敗整筆回滾；交易內只讀寫資料庫，
不呼叫模型、知識服務或價格來源（知識說明、價格與營養都取自 T14／T15 已保存的結果快照）。

1. 以家庭為單位取得交易層級 advisory lock：同家庭的採用與昨日歷史重驗依序進行。
2. 冪等：(household, Idempotency-Key) 已存在 → 同命令回傳同一筆，不同命令 409 IDEMPOTENCY_CONFLICT。
3. 鎖問卷列，重驗問卷版本、結果版本（409 VERSION_CONFLICT）；同一問卷只採用一次。
4. 以 T15 meal_results.eligibility（meal_validation.evaluate＋該版本的決定）重新判定，必須 adoptable；
   以缺價例外採用時，請求必須指名那筆接受紀錄。
5. meal_history.recheck_previous_day 重新讀取前一日已採用紀錄；變動造成撞菜 → 409 PREVIOUS_DAY_DISH_CONFLICT。
6. 寫入 adopted_meal_plans（T11 表，T16 以 ALTER 擴充）與完整快照，問卷改為 confirmed。

快照是深拷貝：來源食譜、價格或知識之後更新都不會改寫歷史。
"""
import copy
from datetime import date, datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, object_session

from app.api.errors import ApiError, not_found, version_conflict
from app.db.models import AdoptedMealPlan, GenerationResult, MealRequest, MealResultDecision
from app.models import Recipe, SourceArchive
from app.services import idempotency, meal_history, meal_results, meal_validation
from app.services.generation import MODE_LABELS

SNAPSHOT_VERSION = "meal_plan_snapshot/v1"
ADOPTION_RULE_VERSION = "t16-adoption/v1"
RECORD_LABEL = "已採用餐食紀錄"
MEAL_TYPE_ORDER = {"breakfast": 0, "lunch": 1, "dinner": 2}


def request_hash(meal_request_id, result_version: int, request_version: int, decision_id) -> str:
    return idempotency.request_hash("adopt", meal_request_id, result_version, request_version, decision_id)


def _lock_household(db: Session, household_id) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"meal-plan:{household_id}"})


def _by_key(db: Session, household_id, key: str, digest: str) -> AdoptedMealPlan | None:
    return idempotency.find_by_key(db, AdoptedMealPlan, household_id, key, digest,
                                   message="這個送出識別碼已用於不同的採用內容，請重新操作。")


def _reject_blocks(blocks: list[dict]) -> ApiError:
    codes = [b["code"] for b in blocks]
    details = {"blocks": blocks}
    if "VERSION_CONFLICT" in codes:
        return ApiError(409, "VERSION_CONFLICT", "問卷已在此結果產生後修改，請重新生成後再採用。", details)
    first = blocks[0]
    return ApiError(422, first["code"], f"此結果不可採用：{first['message']}", details)


def _price_exception(db: Session, result: GenerationResult, evaluation: dict, decision_id) -> MealResultDecision | None:
    accepted = None
    if evaluation["price_exception_accepted"]:
        accepted = db.scalar(select(MealResultDecision).where(
            MealResultDecision.generation_result_id == result.id,
            MealResultDecision.kind == meal_validation.ACCEPT_MISSING_PRICE))
    if accepted is None and decision_id is not None:
        raise ApiError(422, "INVALID_INPUT", "此結果沒有可用的缺價例外接受紀錄。",
                       {"reason": "PRICE_EXCEPTION_NOT_APPLICABLE"})
    if accepted is not None and str(accepted.id) != str(decision_id):
        raise ApiError(422, "INVALID_INPUT", "此結果缺少部分價格，採用時須確認先前接受的缺價例外。",
                       {"reason": "PRICE_EXCEPTION_NOT_CONFIRMED", "missing_food_ids":
                        (accepted.details or {}).get("missing_food_ids")})
    return accepted


def _recipe_sources(db: Session, dishes: list[dict]) -> dict:
    """資料庫菜的來源快照（名稱、網址、匯入批次與內容雜湊）；AI 菜沒有來源食譜。"""
    ids = [d["recipe_id"] for d in dishes if d.get("recipe_id")]
    if not ids:
        return {}
    rows = db.execute(select(Recipe, SourceArchive).join(SourceArchive, SourceArchive.raw_id == Recipe.raw_id)
                      .where(Recipe.recipe_id.in_(ids))).all()
    return {r.recipe_id: {"recipe_id": r.recipe_id, "recipe_name": r.recipe_name, "source_name": r.source_name,
                          "source_url": r.source_url, "review_status": r.review_status,
                          "original_servings": str(r.original_servings) if r.original_servings is not None else None,
                          "import_batch_id": a.import_batch_id, "content_hash": a.content_hash}
            for r, a in rows}


def _snapshot(db: Session, request: MealRequest, result: GenerationResult, evaluation: dict,
              accepted: MealResultDecision | None, recheck: dict) -> dict:
    payload = copy.deepcopy(result.payload)
    final = (payload.get("calculation") or {}).get("final") or {}
    evidence = payload.get("evidence") or {}
    return {
        "schema_version": SNAPSHOT_VERSION,
        "record_label": RECORD_LABEL,
        "adopted_versions": {"meal_request_id": str(request.id), "request_version": request.version,
                             "generation_result_id": str(result.id), "result_version": result.result_version},
        "request": copy.deepcopy(request.payload),
        "result": payload,
        "recipe_sources": _recipe_sources(db, payload["dishes"]),
        "eligibility": {k: copy.deepcopy(evaluation[k]) for k in ("adoptable", "blocks", "price_exception_accepted")},
        "decisions": copy.deepcopy(evaluation["decisions"]),
        "price_exception": {"decision_id": str(accepted.id), **copy.deepcopy(accepted.details or {})}
        if accepted else None,
        "missing": {"price_food_ids": meal_validation.missing_price_food_ids(payload),
                    "price_exception_accepted": evaluation["price_exception_accepted"],
                    "nutrients": copy.deepcopy(final.get("missing_nutrients") or {})},
        "previous_day": {"at_generation": copy.deepcopy(payload.get("previous_day") or {}),
                         "at_adoption": copy.deepcopy(recheck.get("context") or payload.get("previous_day") or {}),
                         "history_changed": bool(recheck.get("history_changed"))},
        "versions": {"result_schema_version": payload.get("schema_version"),
                     "snapshot_schema_version": SNAPSHOT_VERSION, "adoption_rule_version": ADOPTION_RULE_VERSION,
                     "nutrition_rule_version": (payload.get("targets") or {}).get("rule_version"),
                     "pricing_version": evidence.get("pricing_version"),
                     "menu_model": evidence.get("menu_model"),
                     "menu_prompt_version": evidence.get("menu_prompt_version"),
                     "menu_schema_version": evidence.get("menu_schema_version"),
                     "explanation_model": evidence.get("explanation_model"),
                     "explanation_prompt_version": evidence.get("explanation_prompt_version"),
                     "knowledge_index_version": evidence.get("knowledge_index_version"),
                     "embedding_model": evidence.get("embedding_model")},
    }


def confirm_request(request: MealRequest) -> None:
    request.status = "confirmed"
    request.updated_at = datetime.now(timezone.utc)


def adopt(db: Session, *, household_id, user_id, meal_request_id, result_version: int, request_version: int,
          price_exception_decision_id, idempotency_key: str) -> tuple[AdoptedMealPlan, bool]:
    """採用指定結果版本；回傳 (紀錄, 是否新建立)。呼叫端負責單一交易與 commit／rollback。"""
    digest = request_hash(meal_request_id, result_version, request_version, price_exception_decision_id)
    _lock_household(db, household_id)
    existing = _by_key(db, household_id, idempotency_key, digest)
    if existing is not None:
        return existing, False

    request = db.scalar(select(MealRequest).where(MealRequest.id == meal_request_id).with_for_update())
    if request is None or request.household_id != household_id:
        raise not_found("找不到此問卷。")
    adopted = db.scalar(select(AdoptedMealPlan).where(AdoptedMealPlan.meal_request_id == request.id))
    if adopted is not None:
        if adopted.result_version == result_version and adopted.request_version == request_version:
            return adopted, False  # 換了送出識別碼的重複點擊：仍是同一筆採用
        raise ApiError(409, "VERSION_CONFLICT", "此問卷已採用其他結果版本，不能再採用另一版本。",
                       {"reason": "ALREADY_ADOPTED", "adopted_result_version": adopted.result_version})
    if request.status in ("queued", "running"):
        raise ApiError(409, "VERSION_CONFLICT", "此問卷正在生成中，請等待結果。", {"reason": "GENERATION_IN_PROGRESS"})
    if request_version != request.version:
        raise version_conflict()
    result = db.scalar(select(GenerationResult).where(GenerationResult.meal_request_id == request.id,
                                                      GenerationResult.result_version == result_version))
    if result is None:
        raise not_found("找不到此結果版本。")

    evaluation = meal_results.eligibility(db, request, result)
    if not evaluation["adoptable"]:
        raise _reject_blocks(evaluation["blocks"])
    accepted = _price_exception(db, result, evaluation, price_exception_decision_id)
    recheck = meal_history.recheck_previous_day(db, result.payload["previous_day"], result.payload["dishes"])
    if recheck["status"] == "conflict":
        raise ApiError(409, meal_history.CONFLICT_CODE, "生成後前一日的已採用紀錄有變動，此菜單與昨日菜重複，請重新生成。",
                       {"conflicting_dishes": [d["dish_name"] for d in recheck["conflicting_dishes"]],
                        "previous_date": recheck["context"]["previous_date"]})

    draft = request.payload
    plan = meal_history.record_adopted(db, household_id, date.fromisoformat(draft["meal_date"]), draft["meal_type"],
                                       mode=result.mode, dishes=result.payload["dishes"])
    plan.meal_request_id, plan.generation_result_id = request.id, result.id
    plan.result_version, plan.request_version = result.result_version, request.version
    plan.idempotency_key, plan.request_hash, plan.adopted_by = idempotency_key, digest, user_id
    plan.price_exception_accepted = evaluation["price_exception_accepted"]
    plan.snapshot = _snapshot(db, request, result, evaluation, accepted, recheck)
    confirm_request(request)
    db.flush()
    return plan, True


# ---- 讀取（只讀快照，不回頭讀來源或結果表） ----

def summary(plan: AdoptedMealPlan) -> dict:
    snap = plan.snapshot or {}
    return {
        "id": str(plan.id), "meal_date": plan.meal_date.isoformat(), "meal_type": plan.meal_type,
        "mode": plan.mode, "mode_label": MODE_LABELS[plan.mode], "ai_generated": plan.mode == "ai_generated_menu",
        "record_label": RECORD_LABEL, "adopted_at": plan.adopted_at.isoformat(),
        "meal_request_id": str(plan.meal_request_id) if plan.meal_request_id else
        (snap.get("adopted_versions") or {}).get("meal_request_id"),
        "result_version": plan.result_version, "request_version": plan.request_version,
        "price_exception_accepted": plan.price_exception_accepted,
        "dishes": [{"standard_dish_id": d["standard_dish_id"], "dish_name": d["dish_name"],
                    "aliases": d.get("aliases", []), "source": d.get("source")} for d in plan.dishes],
    }


def detail(plan: AdoptedMealPlan) -> dict:
    out = summary(plan)
    snap = plan.snapshot or {}
    payload = snap.get("result")
    if not payload:  # T11 的最小紀錄（沒有完整快照）
        return {**out, "snapshot_available": False}
    sources = snap.get("recipe_sources") or {}
    dishes = meal_results._dishes(payload)
    for d in dishes:
        d["recipe_source"] = sources.get(d["recipe_id"]) if d.get("recipe_id") else None
    targets = payload.get("targets") or {}
    return {
        **out, "snapshot_available": True,
        "fallback_reason": payload.get("fallback_reason"),
        "fallback_message": meal_results.FALLBACK_MESSAGES.get(payload.get("fallback_reason"))
        if payload.get("fallback_reason") else None,
        "dishes": dishes,
        "staple": meal_results.staple_view(payload),
        "request": {k: snap["request"].get(k) for k in ("meal_date", "meal_type", "budget", "staple", "dish_count",
                                                       "specified_dishes", "taste_preferences",
                                                       "available_ingredient_ids")},
        "participations": targets.get("items") or [],
        "targets": {"rule_version": targets.get("rule_version"), "total_n": targets.get("total_n"),
                    "target": targets.get("target")},
        "nutrition": meal_results.nutrition_with_people(object_session(plan), plan.household_id, payload),
        "prices": meal_results._prices(payload),
        "missing": snap.get("missing"),
        "price_exception": snap.get("price_exception"),
        "knowledge": payload.get("explanation") or {"status": "not_found", "items": []},
        "previous_day": {**meal_results._previous_day(payload),
                         "at_adoption": snap["previous_day"]["at_adoption"],
                         "history_changed": snap["previous_day"]["history_changed"]},
        "versions": snap.get("versions"),
    }


def _ordered(rows) -> list[AdoptedMealPlan]:
    return sorted(rows, key=lambda r: (r.meal_date, MEAL_TYPE_ORDER.get(r.meal_type, 9), r.adopted_at))


def on_date(db: Session, household_id, day: date) -> list[AdoptedMealPlan]:
    return _ordered(db.scalars(select(AdoptedMealPlan).where(
        AdoptedMealPlan.household_id == household_id, AdoptedMealPlan.meal_date == day,
        AdoptedMealPlan.status == "adopted")).all())


def trend(db: Session, household_id, since: date) -> list[dict]:
    """歷史圖表用：since 起每筆已採用餐食的整餐熱量、蛋白質、目標與成本（皆取自採用當下快照；未知為 None）。"""
    rows = _ordered(db.scalars(select(AdoptedMealPlan).where(
        AdoptedMealPlan.household_id == household_id, AdoptedMealPlan.meal_date >= since,
        AdoptedMealPlan.status == "adopted")).all())
    out = []
    for plan in rows:
        payload = (plan.snapshot or {}).get("result") or {}
        nutrition = meal_results._nutrition(payload) if payload else {}
        prices = meal_results._prices(payload) if payload else {}
        target = nutrition.get("target") or {}
        out.append({
            "meal_date": plan.meal_date.isoformat(), "meal_type": plan.meal_type,
            "energy_kcal": nutrition.get("energy_kcal"), "protein_g": nutrition.get("protein_g"),
            "target_energy_kcal": target.get("energy_kcal"), "target_protein_g": target.get("protein_g"),
            "cost_total": prices.get("cost_total"), "known_cost_total": prices.get("known_cost_total"),
            "budget": prices.get("budget"),
        })
    return out


def recent_dates(db: Session, household_id, limit: int = 60) -> list[dict]:
    rows = db.execute(select(AdoptedMealPlan.meal_date, func.count(AdoptedMealPlan.id))
                      .where(AdoptedMealPlan.household_id == household_id, AdoptedMealPlan.status == "adopted")
                      .group_by(AdoptedMealPlan.meal_date).order_by(AdoptedMealPlan.meal_date.desc())
                      .limit(limit)).all()
    return [{"meal_date": d.isoformat(), "count": n} for d, n in rows]
