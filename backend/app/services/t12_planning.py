"""資料庫菜單的 T12 營養預覽（食譜庫選菜／換一份菜單時的「營養計算」與採用前重算）。

沿用 T14 生成工作的同一套計算，不另寫一套營養公式：
1. 依問卷與成員以 T03 計算本餐目標（meal_request.target_preview → targets_from_t03）；
2. 食譜列由 generation._database_dishes 載入（與生成結果相同的每 1n 克數與步驟），加上主食列；
3. 先照生成工作對資料庫菜單的做法減鹹（reduce_sodium／meal_sodium_budget），再以
   calculate_meal(mode="database") 驗證（熱量不高於目標 110%、蛋白質不低於目標 90%）。

主食的兩道防線在 T14 只在生成工作內檢查，這條路徑不經過生成工作，所以在這裡重做一次：
主食含過敏／禁吃食材（STAPLE_DIETARY_CONFLICT）、主食本身算不出熱量或蛋白質（STAPLE_NUTRITION_UNAVAILABLE）
都不拋例外，而是回傳 calculation_incomplete 並標示不可採用，畫面照常顯示菜與原因。

未知的營養與克數一律保持 None（不以 0 代替）；每人份量依各成員當餐熱量目標的比例分配。
回傳值的 _calc 是原始計算結果（給採用時計算成本），HTTP 回應前必須以 public_view 移除。
"""
from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.models import Recipe
from app.services import generation, meal_request, per_person_nutrition, seasonings, staples
from app.services.meal_calculation import (REQUIRED_NUTRIENTS, _enough, cached_factor_calculator, calculate_meal,
                                          targets_from_t03)
from app.services.recipe_import import normalize_name, standard_dish_id

GARNISH_PATH = PROJECT_ROOT / "config" / "garnish_ingredients.json"
NUTRIENT_KEYS = ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g", "sugars_g", "sodium_mg", "dietary_fiber_g")
INCOMPLETE_CODES = {"INGREDIENT_UNRESOLVED", "NUTRITION_INCOMPLETE"}
STAPLE_DIETARY_CONFLICT = "STAPLE_DIETARY_CONFLICT"
STAPLE_NUTRITION_UNAVAILABLE = generation.STAPLE_NUTRITION_UNAVAILABLE
STAPLE_CODES = (STAPLE_DIETARY_CONFLICT, STAPLE_NUTRITION_UNAVAILABLE)
# 換算係數快取：畫面依序呼叫營養預覽、營養小知識與採用，各自重算；短時間內重用同一份（只含查表資料，不含連線）
CALCULATOR_TTL_SECONDS = 60


class T12PlanningUnavailable(Exception):
    """食譜或營養資料暫時無法讀取（API 回 503）；不可當成「營養未通過」。"""

    status = 503

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class T12MenuChanged(T12PlanningUnavailable):
    """所選食譜不存在或已不合格（API 回 409 MENU_CHANGED）：重試不會成功，須重新查詢菜單。"""

    status = 409

    def __init__(self, message: str):
        super().__init__("MENU_CHANGED", message)


_calculators: dict = {}
_calculators_lock = threading.Lock()


def _calculator(db: Session):
    """cached_factor_calculator 每次呼叫都重新讀取係數快照；同一資料庫在 TTL 內重用。"""
    key = db.get_bind()
    now = time.monotonic()
    with _calculators_lock:
        cached = _calculators.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
    calculator = cached_factor_calculator(db)
    with _calculators_lock:
        _calculators[key] = (now + CALCULATOR_TTL_SECONDS, calculator)
    return calculator


# ---- 畫面顯示：食材分組與調味料小匙／大匙 ----

@lru_cache(maxsize=2)
def _garnishes(path: str = str(GARNISH_PATH)) -> frozenset[str]:
    p = Path(path)
    return frozenset(json.loads(p.read_text(encoding="utf-8")).get("ingredients", {})) if p.is_file() else frozenset()


@lru_cache(maxsize=2)
def _seasoning_ids(path: str = str(seasonings.SEASONINGS_PATH)) -> frozenset[str]:
    return frozenset(seasonings.load_seasonings(path))


def display_group(food_id: str | None) -> str:
    """食譜畫面的分組：調味（config/seasoning_ingredients.json）／佐料（config/garnish_ingredients.json）／主食材（其餘）。
    只影響畫面，不影響營養計算。"""
    if food_id and food_id in _seasoning_ids():
        return "調味"
    if food_id and food_id in _garnishes():
        return "佐料"
    return "主食材"


def _text(value) -> str | None:
    """精確值以字串輸出（不轉 float）；None 維持 None。"""
    if value is None:
        return None
    if isinstance(value, Fraction):
        return str(Decimal(value.numerator) / Decimal(value.denominator))
    return str(value)


def _with_spoons(ingredient: dict, row: dict) -> dict:
    """調味料改用小匙／大匙顯示；其他食材沿用 T12 的畫面用量（原單位或克）。計算仍用克數。"""
    grams = row.get("whole_meal_grams")
    shown = (seasonings.spoon_display(ingredient["food_id"], grams, ingredient.get("unit"))
             if ingredient.get("food_id") and grams is not None else None)
    if shown is None:
        shown = {"display_amount": row.get("display_amount"), "display_unit": row.get("display_unit"),
                 "display_text": row.get("display_text")}
    return {**ingredient, "group": display_group(ingredient.get("food_id")),
            "display_amount": _text(shown.get("display_amount")), "display_unit": shown.get("display_unit"),
            "display_text": shown.get("display_text")}


# ---- 食譜摘要（與 T11 查詢的 _summary 同欄位） ----

def _summaries(db: Session, draft, recipe_ids: list[str]) -> list[dict]:
    ids = list(dict.fromkeys(recipe_ids))
    try:
        rows = {r.recipe_id: r for r in db.execute(
            select(Recipe.recipe_id, Recipe.recipe_name, Recipe.name_aliases, Recipe.category, Recipe.is_eligible)
            .where(Recipe.recipe_id.in_(ids))).all()}
    except SQLAlchemyError:
        raise T12PlanningUnavailable("RECIPE_DATA_UNAVAILABLE", "食譜資料暫時無法讀取，請稍後重試。") from None
    missing = [rid for rid in ids if rid not in rows or not rows[rid].is_eligible]
    if missing:
        raise T12MenuChanged(f"找不到可用的食譜（{'、'.join(missing)}），請重新查詢菜單。")
    specified = {normalize_name(name) for name in draft.specified_dishes} - {""}
    out = []
    for rid in ids:
        r = rows[rid]
        aliases = [a for a in (r.name_aliases or []) if isinstance(a, str) and a.strip()]
        names = {normalize_name(n) for n in (r.recipe_name, *aliases)}
        out.append({"recipe_id": r.recipe_id, "recipe_name": r.recipe_name,
                    # 與 T11 查詢相同：依菜名重新計算標準菜色 ID（決策 #36）
                    "standard_dish_id": standard_dish_id(r.recipe_name), "category": r.category,
                    "aliases": sorted(set(aliases) - {r.recipe_name}),
                    "specified": bool(specified & names)})
    return out


# ---- 主食防線（T14 _generate 的同一組檢查，改為回報而不拋例外） ----

def _staple_known(staple, calculator) -> bool:
    """與 generation._require_staple_nutrition 相同的判斷：主食可選、有克數，且熱量與蛋白質都算得出來。"""
    ln = staple.line
    if ln.unresolved_reason == "staple_not_supported" or ln.grams_per_n is None:
        return False
    [row] = calculator([(ln, ln.grams_per_n)])
    return all(row.nutrients.get(k) is not None for k in REQUIRED_NUTRIENTS)


def _staple_issues(staple, excluded: set[str], calculator) -> list[dict]:
    if staple is None:
        return []
    if staple.line.food_id in excluded:
        return [{"code": STAPLE_DIETARY_CONFLICT, "recipe_id": "staple", "line_id": None, "field": "staple",
                 "message": f"主食「{staple.label}」含過敏或禁吃食材，請修改主食；這份菜單暫時不能採用。",
                 "adoption_blocked": True}]
    if not _staple_known(staple, calculator):
        if staple.line.unresolved_reason == "staple_not_supported":
            message = f"主食「{staple.label}」不在可選清單中，請從清單改選主食；這份菜單暫時不能採用。"
        else:
            message = f"主食「{staple.label}」目前沒有營養資料，整餐營養無法計算；請改選其他主食。"
        return [{"code": STAPLE_NUTRITION_UNAVAILABLE, "recipe_id": "staple", "line_id": None, "field": "staple",
                 "message": message, "adoption_blocked": True}]
    return []


# ---- 狀態與說明 ----

def _status(calc: dict, issues: list[dict]) -> str:
    if any(i["code"] in STAPLE_CODES for i in issues):
        return "calculation_incomplete"
    if {b["code"] for b in calc["block_reasons"]} & INCOMPLETE_CODES:
        return "calculation_incomplete"
    return "nutrition_passed" if calc["adoptable"] else "outside_tolerance"


def _status_message(status: str, calc: dict, issues: list[dict]) -> str:
    """畫面上的一句說明；只有營養通過且沒有任何阻擋時為空字串。"""
    parts = [i["message"] for i in issues if i["adoption_blocked"] and i["code"] != "INGREDIENT_UNRESOLVED"]
    if status == "nutrition_passed" and not parts:
        return ""
    reasons = calc["block_reasons"]
    codes = {b["code"] for b in reasons}
    if "INGREDIENT_UNRESOLVED" in codes:
        names = "、".join(dict.fromkeys(r["name"] for r in calc["unresolved_ingredients"]))
        parts.append(f"部分食材無法換算克數（{names}），營養無法完成計算，暫時不能採用。")
    elif "NUTRITION_INCOMPLETE" in codes:
        names = [n for b in reasons if b["code"] == "NUTRITION_INCOMPLETE"
                 for ns in (b.get("ingredients") or {}).values() for n in ns]
        detail = f"（{'、'.join(dict.fromkeys(names))}）" if names else ""
        parts.append(f"部分食材缺少熱量或蛋白質資料{detail}，營養無法完成計算，暫時不能採用。")
    elif status == "outside_tolerance":
        out = ["熱量超過目標 110%" if b["nutrient"] == "energy_kcal" else "蛋白質低於目標 90%"
               for b in reasons if b["code"] == "NUTRITION_OUT_OF_RANGE"]
        parts.append(f"營養已計算，但{'、'.join(dict.fromkeys(out))}，在可調份量內無法對齊，不能採用。"
                     if out else "營養計算未通過，不能採用。")
    if not parts:
        parts.append("營養計算未通過，不能採用。")
    return "".join(dict.fromkeys(parts))


# ---- 每人份量（依各成員當餐熱量目標的比例，與 per_person_nutrition.per_person 相同的分法） ----

def _fraction(value) -> Fraction:
    return Fraction(Decimal(str(value)))


def _shares_summing_to_one(shares: list[Fraction]) -> list[Fraction]:
    """畫面用的每份比例：取到 0.01 且合計剛好為 1（最大餘數法）；營養仍用精確比例。"""
    cents = [s * 100 for s in shares]
    floors = [int(c) for c in cents]
    order = sorted(range(len(shares)), key=lambda i: (-(cents[i] - floors[i]), i))
    for i in order[:max(0, 100 - sum(floors))]:
        floors[i] += 1
    return [Fraction(f, 100) for f in floors]


def _per_person(targets_info: dict, totals: dict, known: dict, recipe_ids: list[str], staple_grams) -> list[dict]:
    items = targets_info.get("items") or []
    meal_energy = _fraction(targets_info["target"]["energy_kcal"])
    if not items or not meal_energy:
        return []
    exact = [_fraction(i["scaled_target"]["energy_kcal"]) / meal_energy for i in items]
    whole = {k: None if totals.get(k) is None else _fraction(totals[k]) for k in NUTRIENT_KEYS}
    out = []
    for item, share, shown in zip(items, exact, _shares_summing_to_one(exact)):
        own = {k: _fraction(item["scaled_target"][k]) for k in REQUIRED_NUTRIENTS}
        per_day = _fraction(item["meal_ratio"]) * _fraction(item["n"])
        eaten = {k: None if v is None else v * share for k, v in whole.items()}
        out.append({
            "participation_id": item["participation_id"], "member_id": item["participation_id"].split(":", 1)[0],
            "kind": item["kind"], "meal": item.get("meal"), "n": _text(item["n"]), "share": _text(share),
            # 這一份應占每日的比例（餐別比例 × n），與每日目標（當餐目標 ÷ 該比例）
            "meal_ratio": _text(per_day),
            "daily": {k: _text(own[k] / per_day) for k in REQUIRED_NUTRIENTS},
            "dish_share": _text(shown), "dish_ids": list(recipe_ids),
            "staple_g": _text(_fraction(staple_grams) * shown) if staple_grams is not None else None,
            "target": {k: _text(v) for k, v in own.items()},
            "nutrition": {k: _text(eaten[k]) for k in REQUIRED_NUTRIENTS},
            "nutrition_all": {k: _text(v) for k, v in eaten.items()},
            "nutrition_known_partial": {k: _text(_fraction(v) * share) for k, v in known.items() if v is not None},
            # 與可採用判定同一條規則（meal_calculation._enough）：熱量 ≤ 110%、蛋白質 ≥ 90%；未知一律 False
            "within_10pct": {k: eaten[k] is not None and _enough(eaten[k], k, own) for k in REQUIRED_NUTRIENTS},
        })
    return out


def _portion(part: dict) -> dict:
    intake = dict(part.get("nutrition_all") or {})
    intake.update({k: v for k, v in (part.get("nutrition") or {}).items() if v is not None})
    return {"participation_id": part["participation_id"], "n": part["n"], "share": part["share"],
            "meal": part["meal"], "meal_ratio": part["meal_ratio"], "dish_share": part["dish_share"],
            "dish_ids": part["dish_ids"], "staple_g": part["staple_g"], "intake": intake,
            "known_partial": dict(part.get("nutrition_known_partial") or {})}


def _people(per_participation: list[dict], names: dict[str, str | None]) -> list[dict]:
    """依成員合併每份結果：dining 為這一餐，bento 另列；附每人每日建議。"""
    grouped: dict[str, dict] = {}
    for part in per_participation:
        person = grouped.setdefault(part["member_id"], {
            "member_id": part["member_id"], "name": names.get(part["member_id"]), "kinds": [],
            "dining": None, "bento": None,
            "daily": per_person_nutrition.daily_reference(Decimal(part["daily"]["energy_kcal"]),
                                                          Decimal(part["daily"]["protein_g"]))})
        person["bento" if part["kind"] == "bento" else "dining"] = _portion(part)
        if part["kind"] not in person["kinds"]:
            person["kinds"].append(part["kind"])
    return list(grouped.values())


# ---- 主流程 ----

def preview(db: Session, *, draft: meal_request.MealRequestDraft, members: dict, recipe_ids: list[str]) -> dict:
    """只用伺服器端的問卷、食譜與營養資料計算；不接受前端傳來的營養或克數。"""
    summaries = _summaries(db, draft, recipe_ids)
    targets_info = meal_request.target_preview(draft, members)
    if targets_info.get("status") != "available":
        raise T12PlanningUnavailable("NUTRITION_TARGET_UNAVAILABLE",
                                     targets_info.get("message") or "無法計算本餐營養目標，請檢查成員資料。")
    try:
        excluded = set(meal_request.restrictions(db, draft, members)["excluded_food_ids"])
        calculator = _calculator(db)
        dishes, lines = generation._database_dishes(db, summaries)
    except (SQLAlchemyError, LookupError):
        raise T12PlanningUnavailable("RECIPE_DATA_UNAVAILABLE", "食譜或營養資料暫時無法讀取，請稍後重試。") from None
    staple = staples.resolve(draft.staple)
    staple_lines = [staple.line] if staple else []
    issues = _staple_issues(staple, excluded, calculator)
    for dish in dishes:
        if excluded.intersection(dish["food_ids"]):
            issues.append({"code": "DIETARY_CONFLICT", "recipe_id": dish["recipe_id"], "line_id": None,
                           "field": "recipe_ids", "adoption_blocked": True,
                           "message": f"「{dish['dish_name']}」含過敏或禁吃食材，不能採用。"})

    targets = targets_from_t03(targets_info)
    # 與生成工作對資料庫菜單相同：先減鹹（主食不動），再驗證整餐
    lines, sodium = generation.reduce_sodium(lines, staple_lines, generation.meal_sodium_budget(targets_info),
                                             calculator)
    all_lines = lines + staple_lines
    calc = calculate_meal(all_lines, targets, mode="database", calculator=calculator)
    for ln in lines:  # 主食克數未知已由主食防線說明
        if ln.grams_per_n is None:
            issues.append({"code": "INGREDIENT_UNRESOLVED", "recipe_id": ln.dish_id, "line_id": None,
                           "field": "grams", "adoption_blocked": True,
                           "message": f"「{ln.name}」無法換算克數，營養無法完成計算。"})

    final = calc["final"]
    others = final.get("other_nutrients") or {}
    totals = {"energy_kcal": final.get("energy_kcal"), "protein_g": final.get("protein_g"),
              **{k: others.get(k) for k in NUTRIENT_KEYS if k not in REQUIRED_NUTRIENTS}}
    unknown = [k for k in NUTRIENT_KEYS if totals[k] is None]
    given_known = final.get("known_partial") or {}
    given_missing = final.get("missing_by_nutrient") or {}
    known = {k: given_known.get(k) for k in unknown}

    rows = calc["ingredients"]
    by_dish: dict[str, list[dict]] = {}
    staple_row = None
    for ln, row in zip(all_lines, rows):
        if row.get("role") == "staple":
            staple_row = row
            continue
        by_dish.setdefault(row.get("dish_id"), []).append(_with_spoons({
            "name": row["name"], "food_id": row["food_id"], "unit": ln.unit,
            "grams": _text(row.get("whole_meal_grams")), "line_id": None}, row))
    adjusted = bool(calc["baseline"].get("adjusted"))
    status = _status(calc, issues)
    passed = status == "nutrition_passed"
    staple_grams = staple_row.get("whole_meal_grams") if staple_row else None
    per_part = _per_person(targets_info, totals, known, [s["recipe_id"] for s in summaries], staple_grams)
    names = {str(member_id): getattr(member, "display_name", None) for member_id, member in members.items()}
    return {
        "status": status,
        "status_message": _status_message(status, calc, issues),
        "dish_portions": ({"factors": {rid: _text(v) for rid, v in calc["dish_scales"].items()}}
                          if adjusted else None),
        "per_person": per_part,
        "people": _people(per_part, names),
        "daily_reference_meta": per_person_nutrition.reference_meta(),
        "nutrition_all": {"totals": {k: _text(v) for k, v in totals.items()},
                          "known_partial": {k: _text(v) for k, v in known.items()},
                          "missing_by_nutrient": {k: list(given_missing.get(k) or []) for k in unknown}},
        "dishes": [{"recipe_id": d["recipe_id"], "ingredients": by_dish.get(d["recipe_id"], []),
                    "steps": list(d.get("steps_display") or d.get("steps") or []), "adjusted": adjusted}
                   for d in dishes],
        "targets": {k: _text(v) for k, v in targets_info["target"].items()},
        "input_issues": issues,
        "adoption_blocked": not passed or any(i["adoption_blocked"] for i in issues),
        # 只有營養通過時才有最終食材（營養小知識只依通過驗證的食材查詢；主食不查知識）
        "final_food_ids": sorted({r["food_id"] for r in rows if r.get("role") != "staple"
                                  and r.get("whole_meal_grams") is not None and r["whole_meal_grams"] > 0})
        if passed else [],
        "staple": ({"name": staple.label, "food_id": staple.line.food_id, "final_grams": _text(staple_grams)}
                   if staple else None),
        "nutrition_passed": passed,
        "nutrition": {"totals": {k: _text(v) for k, v in totals.items()}, "missing": unknown},
        "sodium_adjustment": generation._jsonable(generation.finalize_sodium(sodium, calc)),
        "_calc": calc,
    }


def public_view(result: dict) -> dict:
    """HTTP 回應：移除只給採用流程用的原始計算結果。"""
    return {k: v for k, v in result.items() if not k.startswith("_")}
