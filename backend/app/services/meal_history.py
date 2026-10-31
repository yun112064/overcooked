"""T11 MealHistoryService：家庭已採用餐食紀錄與前一日菜色比對（R13）。

比對範圍：「本次規劃的餐食日期減一個日曆日」的全部餐別，加上同一天其他餐別已採用的菜
（2026-10-26 使用者決定：同一天午餐、晚餐也不重複）；沒有紀錄就是沒有，不往更早日期回溯。
同菜以標準菜色 ID／別名判定。
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdoptedMealPlan
from app.services.recipe_import import standard_dish_id

MODES = ("database_menu", "ai_generated_menu")
CONFLICT_CODE = "PREVIOUS_DAY_DISH_CONFLICT"
MEAL_LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}


def dish_keys(dish: dict) -> set[str]:
    """菜色識別集合：標準菜色 ID 加上名稱與每個別名換算的 ID；交集非空即同菜。"""
    keys = {dish["standard_dish_id"]} if dish.get("standard_dish_id") else set()
    for name in (dish.get("dish_name"), *(dish.get("aliases") or [])):
        if isinstance(name, str) and name.strip():
            keys.add(standard_dish_id(name))
    return keys


def same_dish(a: dict, b: dict) -> bool:
    return bool(dish_keys(a) & dish_keys(b))


def _clean_dish(dish: dict) -> dict:
    if not isinstance(dish, dict) or not dish.get("standard_dish_id") or not dish.get("dish_name"):
        raise ValueError("每道已採用菜都必須保存標準菜色 ID 與菜名。")
    if dish.get("source", "database") not in ("database", "ai"):
        raise ValueError("菜色來源只能是 database 或 ai。")
    return {"standard_dish_id": dish["standard_dish_id"], "dish_name": dish["dish_name"],
            "aliases": [a for a in dish.get("aliases") or [] if isinstance(a, str) and a.strip()],
            "source": dish.get("source", "database"), "recipe_id": dish.get("recipe_id")}


def record_adopted(db: Session, household_id, meal_date: date, meal_type: str, *, mode: str,
                   dishes: list[dict], status: str = "adopted", version: int = 1) -> AdoptedMealPlan:
    """寫入一筆已採用快照（測試紀錄與 T16 採用交易共用；呼叫端負責交易）。"""
    if mode not in MODES:
        raise ValueError("模式只能是 database_menu 或 ai_generated_menu。")
    if not dishes:
        raise ValueError("已採用餐食至少要有一道菜。")
    row = AdoptedMealPlan(id=uuid.uuid4(), household_id=household_id, meal_date=meal_date, meal_type=meal_type,
                          mode=mode, status=status, version=version, dishes=[_clean_dish(d) for d in dishes])
    db.add(row)
    db.flush()
    return row


def previous_day_context(db: Session, household_id, meal_date: date, *, specified_dish_ids: list[str],
                         meal_type: str | None = None) -> dict:
    """回傳可保存的 PreviousDayContext；指定菜只豁免它自己。

    給了 meal_type 時，同一天其他餐別已採用的菜也排除（同餐別重新規劃不算）。
    """
    previous = meal_date - timedelta(days=1)
    same_day = (AdoptedMealPlan.meal_date == meal_date) & (AdoptedMealPlan.meal_type != meal_type)
    scope = (AdoptedMealPlan.meal_date == previous) | same_day if meal_type else AdoptedMealPlan.meal_date == previous
    rows = db.scalars(
        select(AdoptedMealPlan)
        .where(AdoptedMealPlan.household_id == household_id, scope, AdoptedMealPlan.status == "adopted")
        .order_by(AdoptedMealPlan.meal_date, AdoptedMealPlan.adopted_at, AdoptedMealPlan.id)
    ).all()
    specified = list(dict.fromkeys(specified_dish_ids))
    excluded: list[dict] = []
    exceptions: list[str] = []
    for row in rows:
        for dish in row.dishes:
            keys = dish_keys(dish)
            hit = [s for s in specified if s in keys]
            if hit:
                exceptions.extend(s for s in hit if s not in exceptions)
                continue
            seen = next((s for s in excluded if same_dish(dish, s)), None)
            if seen is not None:
                # 同一道菜昨天出現多次：合併成一筆，保留所有別名
                for name in (dish["dish_name"], *(dish.get("aliases") or [])):
                    if name != seen["dish_name"] and name not in seen["aliases"]:
                        seen["aliases"].append(name)
                continue
            excluded.append({"standard_dish_id": dish["standard_dish_id"], "dish_name": dish["dish_name"],
                             "aliases": list(dish.get("aliases") or []),
                             # 來源：昨日或同一天哪一餐（畫面說明用）
                             "from": "previous_day" if row.meal_date == previous else row.meal_type})
    return {
        "household_id": str(household_id),
        "meal_date": meal_date.isoformat(),
        "previous_date": previous.isoformat(),
        "meal_type": meal_type,
        "records": [{"id": str(r.id), "version": r.version, "meal_type": r.meal_type,
                     "meal_date": r.meal_date.isoformat()} for r in rows],
        "excluded_dishes": excluded,
        "specified_dish_ids": specified,
        "specified_exceptions": exceptions,
    }


def conflicts(context: dict, dishes: list[dict]) -> list[dict]:
    """菜單中與昨日排除清單同菜的菜色；明確指定的菜（含昨日以其別名記錄者）不算衝突。"""
    return [d for d in dishes if not _is_specified(d, context)
            and any(same_dish(d, ex) for ex in context["excluded_dishes"])]


def _is_specified(dish: dict, context: dict) -> bool:
    return bool(dish_keys(dish).intersection(context.get("specified_dish_ids") or []))


def specified_repeats(context: dict, dishes: list[dict]) -> list[str]:
    """菜單中因明確指定而與昨日重複的菜名（含昨日以別名記錄者）。"""
    exceptions = set(context.get("specified_exceptions") or [])
    return [d["dish_name"] for d in dishes if exceptions & dish_keys(d) or (
        _is_specified(d, context) and any(same_dish(d, ex) for ex in context.get("excluded_dishes") or []))]


def recheck_previous_day(db: Session, context: dict, dishes: list[dict]) -> dict:
    """採用前重新讀取歷史：歷史變動使菜單撞昨日菜時回報衝突，不採用過時結果。"""
    fresh = previous_day_context(db, uuid.UUID(context["household_id"]), date.fromisoformat(context["meal_date"]),
                                 specified_dish_ids=context.get("specified_dish_ids", []),
                                 meal_type=context.get("meal_type"))
    changed = ([(r["id"], r["version"]) for r in fresh["records"]]
               != [(r["id"], r["version"]) for r in context["records"]])  # 舊結果沒有 meal_type：只比前一日，行為不變
    clash = conflicts(fresh, dishes)
    if clash:
        return {"status": "conflict", "code": CONFLICT_CODE, "conflicting_dishes": clash,
                "history_changed": changed, "context": fresh}
    if changed:
        return {"status": "ok", "history_changed": True, "context": fresh}
    return {"status": "ok"}
