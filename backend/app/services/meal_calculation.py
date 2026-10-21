"""MealCalculationService（T12）：基準營養對齊、n 倍與烹調營養驗證。

計算順序（Spec R06）：
1. 以每 1n 的基準用量計算熱量／蛋白質，與 E_reference、P_reference 的 90%–110%（含邊界）比較；
   資料庫模式可在已審核（approved）的可調範圍內調整，AI 模式只驗證候選用量。
2. 基準通過後用量乘總 N，整餐用量重新計算營養，再以 E_target、P_target 的 ±10% 判定。
3. 全程以 Decimal／Fraction 精確值計算與比較；取整只出現在 display_grams（克到 1 g）。

必要營養只有熱量與蛋白質（decisions #4）；其他營養僅顯示，缺值為 None 並列出，不擋採用。
未知營養一律 None，不以 0 代替。純計算核心不碰資料庫，營養值由可注入的 calculator 提供。
"""
import itertools
import sys
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from functools import lru_cache
from typing import Callable

from app.core.config import PROJECT_ROOT
from app.meal_calc.types import EstimatedFactor, IngredientRequirement
from app.services.recipe_steps import ingredient_group

REQUIRED_NUTRIENTS = ("energy_kcal", "protein_g")
LOWER, UPPER = Fraction(9, 10), Fraction(11, 10)


@dataclass(frozen=True)
class ParticipationBaseline:
    """單一用餐／便當項目：餐別基準 B_E、B_P（已乘餐別比例）與 n。"""

    participation_id: str
    meal_energy_kcal: Decimal
    meal_protein_g: Decimal
    n: Decimal


@dataclass(frozen=True)
class MealTargets:
    total_n: Fraction
    energy_target: Fraction
    protein_target: Fraction

    @property
    def energy_reference(self) -> Fraction:
        return self.energy_target / self.total_n

    @property
    def protein_reference(self) -> Fraction:
        return self.protein_target / self.total_n


PORTION_OPTIONS = frozenset(Fraction(v) for v in ("0.7", "0.85", "1", "1.15", "1.3"))


def compose_targets(items: list[ParticipationBaseline]) -> MealTargets:
    """R06：N = Σn；E_target = Σ B_E×n；P_target = Σ B_P×n。

    個別 n 只能是五種選項；合計 N 不受限。同人用餐與便當是兩個項目，各算一次。
    """
    if not items:
        raise ValueError("at least one participation is required")
    ids = [i.participation_id for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("participation ids must be unique")
    for i in items:
        if Fraction(i.n) not in PORTION_OPTIONS:
            raise ValueError(f"n must be one of 0.7, 0.85, 1, 1.15, 1.3 (got {i.n})")
    total_n = sum((Fraction(i.n) for i in items), Fraction())
    energy = sum((Fraction(i.meal_energy_kcal) * Fraction(i.n) for i in items), Fraction())
    protein = sum((Fraction(i.meal_protein_g) * Fraction(i.n) for i in items), Fraction())
    return MealTargets(total_n, energy, protein)


def targets_from_t03(aggregate: dict) -> MealTargets:
    """沿用 T03 aggregate_meal_targets 的精確結果（total_n、target），不另寫一套公式。"""
    return MealTargets(
        Fraction(Decimal(aggregate["total_n"])),
        Fraction(Decimal(aggregate["target"]["energy_kcal"])),
        Fraction(Decimal(aggregate["target"]["protein_g"])),
    )


@dataclass(frozen=True)
class IngredientLine:
    """一項配方食材的每 1n 基準用量（克；依 weight_basis 為生或熟可食重）。

    grams_per_n 為 None 代表用量無法換成克（例如「1 匙」尚無審核換算），不可填 0。
    adjustable_min／max 也是每 1n 克數，只有 adjustment_approved 時資料庫模式才可使用。
    """

    food_id: str
    name: str
    grams_per_n: Decimal | None
    cooking_method: str | None = None
    weight_basis: str = "raw"
    consumed_as_is: bool = False
    adjustable_min: Decimal | None = None
    adjustable_max: Decimal | None = None
    adjustment_approved: bool = False
    dish_id: str | None = None
    unresolved_reason: str | None = None
    # 生熟依據（P6／decisions #17）：工作簿克數一律視為生重；有生熟欄位時優先採用該欄位
    weight_basis_source: str | None = None
    # 顯示用（P4）：原單位與每 1n 原數量（容量單位顯示到 0.5）、原文；不參與計算
    unit: str | None = None
    amount_per_n: Decimal | None = None
    raw_text: str | None = None
    # dish（菜色食材）或 staple（主食，decisions #16：計入營養與 ±10%，不計價格／缺料）
    role: str = "dish"
    # decisions #19：克數由快取的每單位克數推估換算時的係數（EstimatedFactor，kind="unit"）；否則 None
    weight_estimate: EstimatedFactor | None = None
    # 食譜層級有多種烹調方式時（cooking_method 不填）保留全部方式，只用於查組合鍵的推估係數
    cooking_methods: tuple = ()
    # 克數來源（decisions #20）：recipe_grams（原文 g）、workbook（工作簿該列 weight_g，非 LLM 推估）、
    # unit_estimate（快取的每單位克數推估）；未解出或非資料庫模式為 None
    weight_source: str | None = None
    # 顯示用（decisions #41）：raw_text 開頭 [X] 標記的食材分組（如「調味」「醃料」）；不參與計算
    group: str | None = None


@dataclass
class IngredientNutrition:
    """calculator 對單一食材的結果：營養值（Decimal 或 None）、追溯資料、錯誤原因。"""

    nutrients: dict
    trace: dict
    error: str | None = None


Calculator = Callable[[list[tuple[IngredientLine, Decimal]]], list[IngredientNutrition]]

MAX_GRID_POINTS = 200_000


def _within(value: Fraction, reference: Fraction) -> bool:
    return LOWER * reference <= value <= UPPER * reference


def _dec(value: Fraction) -> Decimal:
    """Fraction 轉 Decimal；有限小數為精確值，循環小數僅供顯示（比較一律用 Fraction）。"""
    return Decimal(value.numerator) / Decimal(value.denominator)


def _evaluate(lines, grams, calculator):
    """以指定克數重新計算；回傳 (每項結果, 各營養合計 Fraction 或 None, 缺值食材清單)。"""
    rows = calculator([(ln, g) for ln, g in zip(lines, grams)])
    keys = list(REQUIRED_NUTRIENTS)
    for row in rows:
        keys += [k for k in row.nutrients if k not in keys]
    totals, missing = {}, {}
    for key in keys:
        values = [row.nutrients.get(key) for row in rows]
        absent = [ln.name for ln, v in zip(lines, values) if v is None]
        if absent:
            totals[key], missing[key] = None, absent
        else:
            totals[key] = sum((Fraction(v) for v in values), Fraction())
    return rows, totals, missing


def _range_reasons(stage, totals, references):
    reasons = []
    for key in REQUIRED_NUTRIENTS:
        if not _within(totals[key], references[key]):
            reasons.append({"code": "NUTRITION_OUT_OF_RANGE", "stage": stage, "nutrient": key,
                            "value": _dec(totals[key]), "lower": _dec(LOWER * references[key]),
                            "upper": _dec(UPPER * references[key])})
    return reasons


def _adjustable(ln: IngredientLine) -> bool:
    return ln.adjustment_approved and ln.adjustable_min is not None and ln.adjustable_max is not None


def _align(lines, grams, rows, references):
    """資料庫模式：只在已審核可調範圍內格點搜尋同時符合熱量與蛋白質的用量。

    單一食材的熱量／蛋白質對克數為線性（固定的每克含量），係數取自基準計算結果；
    選中的用量之後仍以 calculator 重新計算確認。格點搜尋找不到即明確回報無解。
    """
    adjustable = [i for i, ln in enumerate(lines) if _adjustable(ln) and grams[i] > 0]
    if not adjustable:
        return None
    per_gram = {i: {k: Fraction(rows[i].nutrients[k]) / Fraction(grams[i]) for k in REQUIRED_NUTRIENTS}
                for i in adjustable}
    fixed = {k: sum((Fraction(rows[i].nutrients[k]) for i in range(len(lines)) if i not in per_gram), Fraction())
             for k in REQUIRED_NUTRIENTS}
    steps = 40
    while (steps + 1) ** len(adjustable) > MAX_GRID_POINTS and steps > 2:
        steps //= 2
    axes = []
    for i in adjustable:
        lo, hi = Fraction(lines[i].adjustable_min), Fraction(lines[i].adjustable_max)
        points = {lo + (hi - lo) * s / steps for s in range(steps + 1)}
        if lo <= Fraction(grams[i]) <= hi:
            points.add(Fraction(grams[i]))
        axes.append(sorted(points))
    best = None
    for combo in itertools.product(*axes):
        totals = {k: fixed[k] + sum((per_gram[i][k] * g for i, g in zip(adjustable, combo)), Fraction())
                  for k in REQUIRED_NUTRIENTS}
        if not all(_within(totals[k], references[k]) for k in REQUIRED_NUTRIENTS):
            continue
        deviation = max(abs(totals[k] / references[k] - 1) for k in REQUIRED_NUTRIENTS)
        moved = sum((abs(g - Fraction(grams[i])) for i, g in zip(adjustable, combo)), Fraction())
        if best is None or (deviation, moved) < best[0]:
            best = ((deviation, moved), combo)
    if best is None:
        return None
    new = list(grams)
    for i, g in zip(adjustable, best[1]):
        new[i] = _dec(g)
    return new


def calculate_meal(lines: list[IngredientLine], targets: MealTargets, *, mode: str,
                   calculator: Calculator) -> dict:
    """驗證整餐並彙整「LLM 推估」標示（decisions #19）；計算本體見 _calculate_meal。"""
    result = _calculate_meal(lines, targets, mode=mode, calculator=calculator)
    result["llm_estimates"] = _llm_summary(result["ingredients"])
    mapped = []
    for row in result["ingredients"]:
        if row.get("llm_mapped") and row["food_id"] not in mapped:
            mapped.append(row["food_id"])
    # decisions #28：哪些食材的 TFDA 樣本來自 LLM 對照推薦（畫面標示「LLM 對照」）
    result["llm_mappings"] = {"used": bool(mapped), "food_ids": mapped}
    result["approximations"] = _approximation_summary(result["ingredients"])
    return result


def _approximation_summary(rows: list[dict]) -> dict:
    """decisions #40：哪些食材、哪些非必要營養素以「TFDA 生組成、保留率視為 1」近似。"""
    food_ids, nutrients = [], []
    for row in rows:
        keys = row.get("approximate_nutrients") or []
        if keys and row["food_id"] not in food_ids:
            food_ids.append(row["food_id"])
        nutrients += [k for k in keys if k not in nutrients]
    return {"used": bool(food_ids), "food_ids": food_ids, "nutrients": nutrients,
            "basis": YIELD_ONLY_RETENTION_1 if food_ids else None}


def _llm_summary(rows: list[dict]) -> dict:
    """結果層級標示：哪些食材、哪些營養使用了 LLM 推估係數（含克數換算影響的全部營養）。"""
    food_ids, nutrients = [], []
    for row in rows:
        if not row.get("llm_estimated"):
            continue
        if row["food_id"] not in food_ids:
            food_ids.append(row["food_id"])
        affected = row.get("estimated_nutrients") or []
        nutrients += [k for k in affected if k not in nutrients]
    return {"used": bool(food_ids), "food_ids": food_ids, "nutrients": nutrients}


def _calculate_meal(lines: list[IngredientLine], targets: MealTargets, *, mode: str,
                    calculator: Calculator) -> dict:
    """驗證整餐：基準對齊 → 乘 N → 重新計算 → ±10% 判定。mode 為 "database" 或 "ai"。

    不可採用時仍盡量可查看（P2）：克數已知的食材一律列出整餐用量與成本需求量（quantities_verified=False），
    並在 suggestions 說明哪個食材往哪個方向調、已審核範圍內是否補得足。
    """
    if mode not in ("database", "ai"):
        raise ValueError("mode must be database or ai")
    if not lines:
        raise ValueError("at least one ingredient line is required")
    references = {"energy_kcal": targets.energy_reference, "protein_g": targets.protein_reference}
    final_targets = {"energy_kcal": targets.energy_target, "protein_g": targets.protein_target}
    n = _dec(targets.total_n)
    result = {
        "mode": mode, "adoptable": False, "block_reasons": [], "quantities_verified": False, "suggestions": [],
        "total_n": n,
        "baseline": {"reference": {k: _dec(v) for k, v in references.items()},
                     "energy_kcal": None, "protein_g": None, "within_range": False, "adjusted": False},
        "final": {"target": {k: _dec(v) for k, v in final_targets.items()},
                  "energy_kcal": None, "protein_g": None, "gap": {}, "within_range": False,
                  "other_nutrients": {}, "missing_nutrients": {}},
        "ingredients": [], "requirements": [], "unresolved_ingredients": [],
    }
    unresolved = [ln for ln in lines if ln.grams_per_n is None]
    if unresolved:
        result["unresolved_ingredients"] = [
            {"food_id": ln.food_id, "name": ln.name, "reason": ln.unresolved_reason or "weight_unresolved"}
            for ln in unresolved]
        result["block_reasons"] += [
            {"code": "INGREDIENT_UNRESOLVED", "food_ids": [ln.food_id for ln in unresolved]},
            {"code": "NUTRITION_INCOMPLETE", "nutrients": list(REQUIRED_NUTRIENTS),
             "reason": "ingredient_weight_unresolved"},
        ]
        result["suggestions"].append({
            "code": "INGREDIENT_UNRESOLVED", "action": "補上克數換算後重新計算",  # 畫面文字（#42 不提 LLM）
            "ingredients": [{"food_id": ln.food_id, "name": ln.name, "unit": ln.unit,
                             "reason": ln.unresolved_reason or "weight_unresolved"} for ln in unresolved]})
        grams = [ln.grams_per_n for ln in lines]
        whole = [g * n if g is not None else None for g in grams]
        result["ingredients"] = [_row(ln, grams[i], whole[i], None, n) for i, ln in enumerate(lines)]
        result["requirements"] = _requirements(lines, whole)
        return result

    grams = [ln.grams_per_n for ln in lines]
    rows, totals, missing = _evaluate(lines, grams, calculator)
    incomplete = [k for k in REQUIRED_NUTRIENTS if totals[k] is None]
    if incomplete:
        result["block_reasons"].append({"code": "NUTRITION_INCOMPLETE", "nutrients": incomplete,
                                        "ingredients": {k: missing[k] for k in incomplete},
                                        "errors": {ln.name: r.error for ln, r in zip(lines, rows) if r.error}})
        names = {name for k in incomplete for name in missing[k]}
        result["suggestions"].append({
            "code": "NUTRITION_INCOMPLETE", "nutrients": incomplete,
            "action": "補齊這些食材的 TFDA 營養資料（或改用有樣品的食材）後重新計算",
            "ingredients": [{"food_id": ln.food_id, "name": ln.name, "error": r.error}
                            for ln, r in zip(lines, rows) if ln.name in names]})
        _unverified_final(result, lines, grams, n, calculator, final_targets)
        return result

    if mode == "database" and not all(_within(totals[k], references[k]) for k in REQUIRED_NUTRIENTS):
        aligned = _align(lines, grams, rows, references)
        if aligned is not None:
            check_rows, check_totals, _ = _evaluate(lines, aligned, calculator)
            if all(check_totals[k] is not None and _within(check_totals[k], references[k])
                   for k in REQUIRED_NUTRIENTS):
                grams, rows, totals = aligned, check_rows, check_totals
                result["baseline"]["adjusted"] = True
    result["baseline"].update(energy_kcal=_dec(totals["energy_kcal"]), protein_g=_dec(totals["protein_g"]))
    baseline_reasons = _range_reasons("baseline", totals, references)
    if baseline_reasons:
        # 無可行方案：不移動目標或容差，只回報差距、可調食材與調整建議
        result["block_reasons"] += baseline_reasons
        result["block_reasons"].append({
            "code": "NO_FEASIBLE_BASELINE", "mode": mode,
            "adjustable_food_ids": [ln.food_id for ln in lines if mode == "database" and _adjustable(ln)]})
        result["suggestions"] = _range_suggestions(lines, grams, rows, totals, references, mode)
        _unverified_final(result, lines, grams, n, calculator, final_targets)
        return result
    result["baseline"]["within_range"] = True

    whole = [g * n for g in grams]
    final_rows, final_totals, final_missing = _evaluate(lines, whole, calculator)
    result["ingredients"] = [_row(ln, grams[i], whole[i], final_rows[i], n) for i, ln in enumerate(lines)]
    result["requirements"] = _requirements(lines, whole)
    final = result["final"]
    final["missing_nutrients"] = final_missing
    final["other_nutrients"] = {k: (_dec(v) if v is not None else None)
                                for k, v in final_totals.items() if k not in REQUIRED_NUTRIENTS}
    final_incomplete = [k for k in REQUIRED_NUTRIENTS if final_totals[k] is None]
    if final_incomplete:
        result["block_reasons"].append({"code": "NUTRITION_INCOMPLETE", "nutrients": final_incomplete,
                                        "stage": "final"})
        return result
    final.update(energy_kcal=_dec(final_totals["energy_kcal"]), protein_g=_dec(final_totals["protein_g"]),
                 gap={k: _dec(final_totals[k] - final_targets[k]) for k in REQUIRED_NUTRIENTS})
    final_reasons = _range_reasons("final", final_totals, final_targets)
    result["block_reasons"] += final_reasons
    final["within_range"] = not final_reasons
    result["quantities_verified"] = not final_reasons
    result["adoptable"] = not result["block_reasons"]
    return result


def _unverified_final(result, lines, grams, n, calculator, final_targets) -> None:
    """不可採用但克數都已知：仍以整餐用量重新計算並列出（未驗證），供查看與成本計算。"""
    whole = [g * n for g in grams]
    rows, totals, missing = _evaluate(lines, whole, calculator)
    result["ingredients"] = [_row(ln, grams[i], whole[i], rows[i], n) for i, ln in enumerate(lines)]
    result["requirements"] = _requirements(lines, whole)
    final = result["final"]
    final["missing_nutrients"] = missing
    final["other_nutrients"] = {k: (_dec(v) if v is not None else None)
                                for k, v in totals.items() if k not in REQUIRED_NUTRIENTS}
    for k in REQUIRED_NUTRIENTS:
        if totals[k] is not None:
            final[k] = _dec(totals[k])
            final["gap"][k] = _dec(totals[k] - final_targets[k])


def _range_suggestions(lines, grams, rows, totals, references, mode) -> list[dict]:
    """每個超出範圍的必要營養：要往哪個方向調、差多少，以及各食材在已審核可調範圍內最多能補多少。

    within_adjustable_ranges 只就該營養單獨判斷（把可調食材都推到邊界）；兩種營養同時滿足的組合
    已由對齊搜尋確認找不到。
    """
    out = []
    for key in REQUIRED_NUTRIENTS:
        lower, upper = LOWER * references[key], UPPER * references[key]
        total = totals[key]
        if lower <= total <= upper:
            continue
        increase = total < lower
        needed = (lower - total) if increase else (total - upper)
        items, reachable = [], Fraction()
        for ln, g, row in zip(lines, grams, rows):
            value = row.nutrients.get(key)
            if value is None or not g:
                continue
            per_gram = Fraction(value) / Fraction(g)
            if per_gram <= 0:
                continue
            approved = mode == "database" and _adjustable(ln)
            change = None
            if approved:
                bound = Fraction(ln.adjustable_max if increase else ln.adjustable_min)
                change = max(Fraction(), (bound - Fraction(g)) * per_gram if increase
                             else (Fraction(g) - bound) * per_gram)
                reachable += change
            items.append((per_gram, {
                "food_id": ln.food_id, "name": ln.name, "direction": "increase" if increase else "decrease",
                "baseline_grams": g, "adjustable_min": ln.adjustable_min, "adjustable_max": ln.adjustable_max,
                "adjustment_approved": approved, "max_change": _dec(change) if change is not None else None}))
        items.sort(key=lambda item: -item[0])
        out.append({"code": "NUTRITION_OUT_OF_RANGE", "nutrient": key,
                    "direction": "increase" if increase else "decrease",
                    "value": _dec(total), "lower": _dec(lower), "upper": _dec(upper), "needed_change": _dec(needed),
                    "within_adjustable_ranges": reachable >= needed,
                    "ingredients": [item for _, item in items[:5]]})
    return out


def _display_grams(value: Decimal) -> Decimal:
    """僅供畫面：克數取到 1 g（四捨五入）。驗證與 T13 一律使用未取整值。"""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def display_count(value: Decimal) -> Decimal:
    """僅供畫面（decisions #43，取代 #3 的 0.5）：非克重單位（顆、片、匙、大匙、杯…）四捨五入成整數，
    大於 0 者最少顯示 1（蛋 4.72 顆 → 5 顆；0.3 小匙 → 1 小匙）。計算一律使用未取整值。"""
    shown = Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return Decimal(1) if value > 0 and shown < 1 else shown


def _display(ln: IngredientLine, whole: Decimal | None, n: Decimal) -> dict:
    """畫面用量（decisions #21／#43）：有原數量＋原單位（非 g）→ 一律顯示原單位、數量取整數（最少 1），
    克數只留在追溯欄位；原單位是 g 或沒有原單位但有克數 → 克到 1 g；都沒有 → 原文。
    縮放倍率：有克數時用整餐克數 ÷ 每 1n 克數（含熱量對齊調整），否則用 n。"""
    if ln.amount_per_n is not None and ln.unit and ln.unit != "g":
        factor = whole / ln.grams_per_n if whole is not None and ln.grams_per_n else n
        return {"display_amount": display_count(ln.amount_per_n * factor), "display_unit": ln.unit,
                "display_text": None}
    if whole is not None:
        return {"display_amount": _display_grams(whole), "display_unit": "g", "display_text": None}
    return {"display_amount": None, "display_unit": None, "display_text": ln.raw_text or ln.name}


def _estimates(ln: IngredientLine, nutrition: IngredientNutrition | None) -> tuple[list[dict], list[str]]:
    """每項食材使用的快取係數標示與受影響營養。克數推估影響該食材全部已算出的營養。"""
    markers = [ln.weight_estimate.marker()] if ln.weight_estimate is not None else []
    trace = nutrition.trace if nutrition and nutrition.trace else {}
    markers += list(trace.get("estimated_factors") or [])
    affected = list(trace.get("estimated_nutrients") or [])
    if ln.weight_estimate is not None and nutrition is not None:
        affected = [k for k, v in nutrition.nutrients.items() if v is not None]
    return markers, affected


def _row(ln: IngredientLine, grams, whole, nutrition: IngredientNutrition | None, n: Decimal) -> dict:
    markers, affected = _estimates(ln, nutrition)
    mapping = ((nutrition.trace or {}).get("tfda_mapping") if nutrition else None) or {}
    return {
        # decisions #28：TFDA 樣本對照來源（exact／alias／synonym／human／llm_mapped）
        "tfda_mapping_source": mapping.get("source"), "tfda_sample_id": mapping.get("sample_id"),
        "llm_mapped": bool(mapping.get("llm_mapped")),
        "estimates": markers, "llm_estimated": any(m["llm_estimated"] for m in markers),
        "estimated_nutrients": affected if any(m["llm_estimated"] for m in markers) else [],
        # decisions #40：保留率視為 1 的近似值（畫面標示「近似」）
        "approximate_nutrients": [k for k in ((nutrition.trace or {}).get("approximate_nutrients") or [])
                                  if nutrition.nutrients.get(k) is not None] if nutrition else [],
        "food_id": ln.food_id, "name": ln.name, "dish_id": ln.dish_id, "role": ln.role, "group": ln.group,
        # calculator 實際採用的生熟依據（decisions #35 可能把工作簿慣例的生重改為熟重）
        "weight_basis": ((nutrition.trace or {}).get("weight_basis") if nutrition else None) or ln.weight_basis,
        "weight_basis_source": (((nutrition.trace or {}).get("weight_basis_source") if nutrition else None)
                                or ln.weight_basis_source),
        "weight_source": ln.weight_source,
        "cooking_method": ln.cooking_method,
        "baseline_grams": grams, "whole_meal_grams": whole,
        "display_grams": _display_grams(whole) if whole is not None else None,
        **_display(ln, whole, n),
        "nutrients": nutrition.nutrients if nutrition else None,
        "trace": nutrition.trace if nutrition else None,
        "error": nutrition.error if nutrition else ((ln.unresolved_reason or "weight_unresolved")
                                                    if grams is None else None),
    }


# 生熟依據代碼（寫入每項食材的 trace，結果可追溯）
WORKBOOK_RAW_CONVENTION = "recipe_workbook_raw_convention"  # decisions #17：工作簿克數＝生重
RECIPE_FIELD = "recipe_weight_basis_field"                  # 未來工作簿有生熟欄位時
MODEL_DECLARED = "model_declared"                           # AI 菜：模型逐項標示 raw／cooked
COOKED_FOOD_SAMPLE = "cooked_food_cooked_sample"            # decisions #35：熟食名稱＋TFDA 熟樣本 → 熟重

# decisions #33：水（及僅為水的食材）營養為已知 0，不需 TFDA 對照
WATER_KNOWN_ZERO = "water_known_zero"
WATER_NAMES = frozenset({"水", "清水", "開水", "冷水", "熱水", "溫水", "滾水", "冰水", "飲用水", "自來水"})
# decisions #35：食材名稱本身就是熟食（工作簿克數即熟重）；只在對照到的 TFDA 樣本為 cooked 時才生效
COOKED_FOOD_SUFFIXES = ("飯", "粥")


@lru_cache(maxsize=1)
def _water_food_ids() -> frozenset[str]:
    """價格端「水免計價」設定（config/price_exempt_ingredients.json）中名稱為水者的 FOOD 編號。"""
    from app.source_adapters.price_collection import PRICE_EXEMPT_PATH
    import json

    try:
        entries = json.loads(PRICE_EXEMPT_PATH.read_text(encoding="utf-8"))["ingredients"]
    except (OSError, ValueError, KeyError):
        return frozenset()
    return frozenset(food_id for food_id, entry in entries.items() if (entry or {}).get("name") in WATER_NAMES)


def is_water(food_id: str | None, name: str | None) -> bool:
    """水的判定：FOOD 編號屬於水免計價清單中的水，或標準名稱就是水（滾水、開水等）。"""
    return bool((name and name.strip() in WATER_NAMES) or (food_id and food_id in _water_food_ids()))


def is_cooked_food_name(name: str | None) -> bool:
    name = (name or "").strip()
    return bool(name) and (name.endswith(COOKED_FOOD_SUFFIXES) or "熟" in name)


def _weight_basis(row) -> tuple[str, str]:
    """有生熟欄位（raw／cooked）時優先；否則依資料規範視為生重。"""
    field = getattr(row, "weight_basis", None)
    if field in ("raw", "cooked"):
        return field, RECIPE_FIELD
    return "raw", WORKBOOK_RAW_CONVENTION


def cached_factor_calculator(db) -> Calculator:
    """生成時使用的 TFDA calculator：載入一次換算係數快取（decisions #19）與 TFDA 對照（別名、同義詞、
    對照快取；decisions #28），計算期間不呼叫 LLM。"""
    from app.estimation.store import load_snapshot
    from app.services.tfda_mapping import load_resolver

    snapshot = load_snapshot(db)
    return make_tfda_calculator(cooking_factors=snapshot.cooking, tfda_resolver=load_resolver(db, snapshot),
                                nutrient_fills=snapshot.nutrient_fill)


def load_recipe_lines(db, recipe_id: str, factors=None) -> list[IngredientLine]:
    """資料庫模式：把 T01 食譜的全部食材（含油與調味料）轉成每 1n 基準用量。

    - 每 1n 克數 = weight_g ÷ original_servings；原份數未知或無克數換算時為 None 並附原因。
    - 克數來源順序（decisions #20）：單位為 g → 原文克數；否則工作簿該列 weight_g（source_weight_g，
      視為生重，來源 workbook，不標示 LLM 推估）；工作簿缺值時才用快取的每單位克數推估
      （factors：FactorSnapshot，未提供則自行載入；人工值優先，rejected 不用），換算後的列帶
      weight_estimate 供結果標示與追溯。都沒有即維持未知。
    - 可調範圍同樣換算為每 1n，只有 adjustment_status = approved 才可用於調整。
    - 烹調方式取自食譜層級：恰好一種時套用；多種時不猜（cooking_method 不填），只保留全部方式
      （cooking_methods）以組合鍵查快取的推估係數（decisions #19），沒有推估值即不可採用。
    - 生熟（decisions #17）：工作簿克數一律視為生重（weight_basis=raw，依據 WORKBOOK_RAW_CONVENTION）；
      若資料表日後有生熟欄位，優先採用該欄位（依據 RECIPE_FIELD）。依據寫入 trace。
    """
    from sqlalchemy import select

    from app.models import Recipe, RecipeIngredient

    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise LookupError(recipe_id)
    servings = recipe.original_servings
    methods = recipe.cooking_methods or []
    method = methods[0] if len(methods) == 1 else None
    rows = db.scalars(select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
                      .order_by(RecipeIngredient.sequence)).all()

    def per_n(value):
        return None if value is None or not servings else Decimal(value) / Decimal(servings)

    if factors is None:
        from app.estimation.store import load_snapshot
        factors = load_snapshot(db)

    lines = []
    for row in rows:
        reason, weight, estimate = None, row.weight_g, None
        source = "recipe_grams" if weight is not None else None
        if weight is None and row.unit != "g" and row.source_weight_g is not None:
            weight, source = row.source_weight_g, "workbook"
        if not servings:
            reason = "servings_unresolved"
        elif weight is None:
            # decisions #19：有數量、非克單位時，改用快取的每單位克數推估（人工值優先；rejected 不用）
            estimate = factors.unit(row.food_id, row.unit) if row.amount is not None and row.unit else None
            if estimate is not None and row.unit != "g":
                weight, source = Decimal(row.amount) * estimate.grams_per_unit, "unit_estimate"
            else:
                estimate, reason = None, row.weight_status or "weight_unresolved"
        basis, basis_source = _weight_basis(row)
        lines.append(IngredientLine(
            food_id=row.food_id, name=row.canonical_name, weight_estimate=estimate,
            grams_per_n=None if reason else per_n(weight),
            cooking_method=method, weight_basis=basis, weight_basis_source=basis_source,
            adjustable_min=per_n(row.adjustable_min), adjustable_max=per_n(row.adjustable_max),
            adjustment_approved=row.adjustment_status == "approved",
            dish_id=recipe_id, unresolved_reason=reason,
            cooking_methods=tuple(methods) if len(methods) > 1 else (),
            unit=row.unit, amount_per_n=per_n(row.amount), raw_text=row.raw_text,
            weight_source=None if reason else source, group=ingredient_group(row.raw_text)[0],
        ))
    return lines


def _nutrition_model():
    """T04 nutrition_model 使用平面匯入，需把目錄放上 sys.path（Docker 映像亦複製此目錄）。"""
    path = str(PROJECT_ROOT / "nutrition_model")
    if path not in sys.path:
        sys.path.append(path)
    import nutrition_service

    return nutrition_service


def _sample_source(row: dict) -> dict | None:
    for source in (row.get("nutrient_sources") or {}).values():
        return source.get("source")
    return (row.get("nutrition_source") or row.get("source")
            or (row.get("raw_reference") or {}).get("source"))


REFERENCE_GRAMS = Decimal(100)


def _scaled(value, grams: Decimal) -> Decimal | None:
    """T04 以 100 g 計算的值（float）→ Decimal(str()) 後在 T12 內以 Decimal 乘上實際克數。"""
    if value is None:
        return None
    return Decimal(str(value)) * grams / REFERENCE_GRAMS


def _no_request_time_llm(payload):
    """計算期間絕不呼叫 LLM（decisions #19）：T04 估算器一律回報未設定，推估值只來自批次快取。

    T04 的 estimator=None 會改用環境變數設定的估算器，因此必須明確傳入此函式。
    """
    return {"success": False, "error": "llm_not_configured"}


def normalize_cooking_method(method: str | None) -> str | None:
    """烹調方式正規化（沿用 T04 cooking_yield_service），作為快取係數的查表鍵。"""
    if not method:
        return None
    _nutrition_model()
    import cooking_yield_service

    return cooking_yield_service.normalize_method(method) or None


def combined_method_key(methods) -> str | None:
    """多種烹調方式的組合鍵：各方式正規化、去重、排序後以「+」連接（批次推估與查表共用）。"""
    keys = sorted({normalize_cooking_method(m) for m in methods or () if m and str(m).strip()} - {None})
    return "+".join(keys) or None


def _method_key(ln: IngredientLine) -> str | None:
    if ln.cooking_method:
        return normalize_cooking_method(ln.cooking_method)
    return combined_method_key(ln.cooking_methods) if len(ln.cooking_methods) > 1 else None


CookingFactorLookup = Callable[[str, str], "EstimatedFactor | None"]

# decisions #40：非必要營養素烹調後的近似依據（TFDA 生組成 × 生重，保留率視為 1；產率只換算熟重）
YIELD_ONLY_RETENTION_1 = "yield_only_retention_1"


def _raw_composition(service, name: str):
    """套保留率用的生樣品：TFDA 狀態不是 cooked 者（raw 或 unspecified，如大蒜 E2100101「乾貨」）。

    T04 烹調管線只接受 state=raw；T12 依 decisions #17 把食譜克數視為生重，未標生熟的 TFDA 樣本即視為
    未烹調的組成，與 T04 無烹調方式時（composition used as supplied）採用同一筆樣本。
    """
    record = service.find_ingredient(name)
    return record if record and record.get("state") != "cooked" else None


def _apply_cooking_factor(service, ln: IngredientLine, grams: Decimal, factor, nutrients: dict, trace: dict,
                          tfda_name: str | None = None) -> None:
    """T04 main 設計：係數（產率／保留率）來自快取，營養由此處以 Decimal 計算。

    - TFDA 有熟樣品但缺產率 → 熟樣品每 100 g × 生重 × 推估產率；
    - 只有生樣品 → 生樣品每 100 g × 生重 × 該營養的推估保留率（不含另列的油、鹽、醬）；
    - 非必要營養素沒有保留率但有產率 → 生樣品每 100 g × 生重（保留率視為 1，decisions #40），標示近似。
    已由 TFDA／USDA 實測算出的營養不覆蓋（實測優先）；沒有推估值的營養維持 None。
    """
    name = tfda_name or ln.name
    raw = _raw_composition(service, name)
    # 熟樣品只對應單一烹調方式；多種方式組合只能套保留率
    cooked = service.find_cooked_ingredient(name, ln.cooking_method) if ln.cooking_method else None
    retention = factor.retention or {}
    used, approximate = [], []
    for key in service.NUTRIENTS:
        if nutrients.get(key) is not None:
            continue
        cooked_value = (cooked or {}).get("nutrition_per_100g", {}).get(key) if cooked else None
        raw_value = raw["nutrition_per_100g"].get(key) if raw else None
        if service.finite_number(cooked_value) and factor.yield_factor is not None:
            nutrients[key] = Decimal(str(cooked_value)) * grams * factor.yield_factor / REFERENCE_GRAMS
            used.append(key)
        elif service.finite_number(raw_value) and retention.get(key) is not None:
            nutrients[key] = Decimal(str(raw_value)) * grams / REFERENCE_GRAMS * retention[key]
            used.append(key)
        elif (key not in REQUIRED_NUTRIENTS and service.finite_number(raw_value)
              and factor.yield_factor is not None):
            approximate.append((key, raw_value))
    if not used:
        return
    # decisions #40：非必要營養素沒有保留率時，以 TFDA 生組成、保留率視為 1 近似（產率只換算熟重）；
    # 不算 LLM 推估的營養，另列 approximate_nutrients 供畫面標示「近似」。TFDA 生組成空白者仍為 None。
    for key, raw_value in approximate:
        nutrients[key] = Decimal(str(raw_value)) * grams / REFERENCE_GRAMS
    if approximate:
        trace["approximate_nutrients"] = [k for k, _ in approximate]
        trace["nutrient_basis"] = {k: YIELD_ONLY_RETENTION_1 for k, _ in approximate}
    basis = "human" if factor.source == "human" else "llm_estimated"
    trace["estimated_factors"].append(factor.marker())
    trace["estimated_nutrients"] = used
    trace["is_estimated"] = True
    if factor.yield_factor is not None and trace.get("cooked_weight_g") is None:
        trace["cooked_weight_g"] = grams * factor.yield_factor
        trace["yield_basis"] = basis
    trace["nutrition_basis"] = ("cooked_tfda_" if cooked else "raw_tfda_") + basis
    if trace.get("sample_id") is None:
        source = (cooked or raw)["source"]
        trace.update(sample_id=source.get("source_id"), provider=source.get("provider"),
                     source_version=source.get("source_version"), source_url=source.get("source_url"))
    if all(nutrients.get(k) is not None for k in REQUIRED_NUTRIENTS):
        trace["missing_reason"] = None


NutrientFillLookup = Callable[[str, str], "EstimatedFactor | None"]


def make_tfda_calculator(*, cooking_factors: CookingFactorLookup | None = None,
                         tfda_resolver=None, nutrient_fills: NutrientFillLookup | None = None) -> Calculator:
    """建立 TFDA calculator；cooking_factors(food_id, 正規化烹調方式) 回傳快取係數或 None。

    tfda_resolver（decisions #28，app.services.tfda_mapping.TfdaResolver）：食譜名稱 → TFDA 樣本；
    未提供時只用精確名稱（T04 原行為）。
    nutrient_fills(TFDA 樣本 ID, 營養素)（decisions #32／#45）：TFDA 樣本 7 種營養素欄位空白時的快取推估（每 100 g）；
    符合「類別必然為 0」規則（config/nutrient_category_zero.json）者一律先補已知 0，不查推估（見 _fill_blanks）。

    優先順序（decisions #19／#32）：TFDA／USDA 實測 > 人工核可／覆寫 > LLM 推估；rejected 永不使用。
    人工與 LLM 的先後由查表端（app.estimation.store）決定，此處只在 T04 實測算不出必要營養時才套用。
    """
    def calculate(rows: list[tuple[IngredientLine, Decimal]]) -> list[IngredientNutrition]:
        return _tfda_calculate(rows, cooking_factors, tfda_resolver, nutrient_fills)
    return calculate


def _water_nutrition(ln: IngredientLine, grams: Decimal, service) -> IngredientNutrition:
    """decisions #33：水的全部營養為已知 0（不是缺值），不查 TFDA。"""
    trace = {"sample_id": None, "provider": None, "source_version": None, "source_url": None,
             "nutrition_basis": WATER_KNOWN_ZERO, "nutrition_source": WATER_KNOWN_ZERO,
             "weight_basis": ln.weight_basis, "weight_basis_source": ln.weight_basis_source,
             "yield_basis": None, "raw_weight_g": grams, "cooked_weight_g": None, "is_estimated": False,
             "missing_reason": None, "estimated_factors": [], "estimated_nutrients": [], "tfda_mapping": None,
             "category_zero": {}}
    return IngredientNutrition({k: Decimal(0) for k in service.NUTRIENTS}, trace, None)


def _effective_basis(ln: IngredientLine, res) -> tuple[str, str | None, str | None]:
    """(weight_basis, 依據, 烹調方式)。decisions #35：工作簿慣例（#17 生重）下，對照到 TFDA 熟樣本且食材名稱
    本身就是熟食（白米飯、白飯…）時改視為熟重；熟樣本已是熟的狀態，不再套烹調方式（另列的油鹽照算）。"""
    from app.services import tfda_mapping

    record = tfda_mapping.sample(res.sample_id) if res else None
    if (ln.weight_basis == "raw" and ln.weight_basis_source == WORKBOOK_RAW_CONVENTION and record
            and record.get("state") == "cooked" and is_cooked_food_name(ln.name)):
        return "cooked", COOKED_FOOD_SAMPLE, None
    return ln.weight_basis, ln.weight_basis_source, ln.cooking_method


def _tfda_calculate(rows, cooking_factors, tfda_resolver=None, nutrient_fills=None) -> list[IngredientNutrition]:
    """以 T04 TFDA 管線計算每項食材（計算期間不呼叫 LLM，見 _no_request_time_llm）。

    為了讓 ±10% 邊界精確（AC12），克數不轉 float 送進 T04：每項食材一律以 100 g 呼叫 T04 取得
    「每 100 g」的營養（及生熟重比例），再以 Decimal(str()) 取值、在 T12 內以 Decimal 乘克數。
    T04 的營養對重量為線性（固定的每克含量與熟成率），因此結果與直接以實際克數計算相同，只是沒有
    float 誤差。缺值維持 None。生熟依 weight_basis 傳入，熟食樣品配熟重時不再套烹調修正（由 T04 保證）。
    水（decisions #33）不送 T04，營養為已知 0。
    """
    from app.services import tfda_mapping

    service = _nutrition_model()
    resolver = tfda_resolver or tfda_mapping.EXACT_ONLY
    water = [is_water(ln.food_id, ln.name) for ln, _ in rows]
    resolutions = [None if w else resolver.resolve(ln.food_id, ln.name) for (ln, _), w in zip(rows, water)]
    bases = [_effective_basis(ln, res) for (ln, _), res in zip(rows, resolutions)]
    items = []
    for (ln, _), res, (basis, _, method), w in zip(rows, resolutions, bases, water):
        if w:
            continue
        item = {"name": res.tfda_name if res else ln.name, "weight_g": float(REFERENCE_GRAMS),
                "weight_basis": basis, "consumed_as_is": ln.consumed_as_is}
        if method:
            item["cooking_method"] = method
        items.append(item)
    recipe = service.calculate_recipe_nutrition("meal", items, estimator=_no_request_time_llm) if items else {}
    t04_rows = iter(recipe.get("ingredients") or [{}] * len(items))
    out = []
    for (ln, grams), res, (basis, basis_source, method), w in zip(rows, resolutions, bases, water):
        grams = Decimal(grams)
        if w:
            out.append(_water_nutrition(ln, grams, service))
            continue
        row = next(t04_rows)
        tfda_name = res.tfda_name if res else ln.name
        values = row.get("nutrition") if row.get("success") else None
        nutrients = {k: _scaled(v, grams) for k, v in (values or {k: None for k in service.NUTRIENTS}).items()}
        source = _sample_source(row) or {}
        trace = {
            "sample_id": source.get("source_id"), "provider": source.get("provider"),
            "source_version": source.get("source_version"), "source_url": source.get("source_url"),
            "nutrition_basis": row.get("nutrition_basis"), "weight_basis": basis,
            "weight_basis_source": basis_source,
            "yield_basis": row.get("yield_basis"), "raw_weight_g": _scaled(row.get("raw_weight_g"), grams),
            "cooked_weight_g": _scaled(row.get("cooked_weight_g"), grams),
            "is_estimated": bool(row.get("is_estimated")), "missing_reason": row.get("missing_reason"),
            "estimated_factors": [], "estimated_nutrients": [], "approximate_nutrients": [],
            "tfda_mapping": res.trace() if res else None, "category_zero": {},
        }
        method_key = _method_key(ln)  # 熟食熟重（#35）時 basis 為 cooked，不套烹調係數
        # T04 烹調管線不接受 unspecified 狀態的樣本（回報 ingredient_not_found）；有生組成時仍可套快取係數
        usable_row = row.get("success") or (row.get("error") == "ingredient_not_found"
                                            and _raw_composition(service, tfda_name) is not None)
        applied, factor = False, None
        if (cooking_factors and method_key and basis == "raw" and usable_row
                and any(nutrients.get(k) is None for k in REQUIRED_NUTRIENTS)):
            factor = cooking_factors(ln.food_id, method_key)
            if factor is not None and factor.usable:
                _apply_cooking_factor(service, ln, grams, factor, nutrients, trace, tfda_name)
                applied = bool(trace["estimated_factors"])
        if row.get("success") or applied:
            _fill_blanks(nutrient_fills, grams, nutrients, trace, factor if applied else None, basis)
        error = (None if applied else row.get("error")) or (
            None if row.get("success") or applied else "nutrition_unavailable")
        out.append(IngredientNutrition(nutrients, trace, error))
    return out


def _fill_weight(grams: Decimal, record: dict, trace: dict, cooking_factor, weight_basis: str) -> Decimal:
    """每 100 g 推估值要乘的重量：熟樣本配生重時用熟重（trace 的熟重，或生重 × 快取產率），其餘照克數。"""
    if record.get("state") == "cooked" and weight_basis == "raw":
        if trace.get("cooked_weight_g") is not None:
            return Decimal(trace["cooked_weight_g"])
        if cooking_factor is not None and cooking_factor.yield_factor is not None:
            return grams * cooking_factor.yield_factor
    return grams


def _fill_blanks(lookup: NutrientFillLookup | None, grams: Decimal, nutrients: dict, trace: dict,
                 cooking_factor, weight_basis: str = "raw") -> None:
    """TFDA 樣本營養欄位空白時補值（TFDA 有值一律以 TFDA 為準；不以 0 代替未知）：

    1. decisions #45：符合「食品類別上必然為 0」規則（config/nutrient_category_zero.json）→ 已知 0，
       trace.category_zero 記錄規則與樣本；不算推估、不呼叫 LLM；
    2. decisions #32／#45：其餘 7 種營養素以快取的每 100 g 推估（kind=nutrient_fill）補上，推估值視為該樣本的組成：
       - 熟樣本 → × 熟重（見 _fill_weight）；
       - 生樣本已套快取烹調係數者 → × 生重 × 該營養保留率（非必要營養素沒有保留率，視為 1，decisions #40）；
       - 否則照克數換算。
    至少要有一項營養已由該樣本算出才補，避免只補出孤立的營養。
    """
    from app.services import nutrient_category_zero, tfda_mapping

    record = tfda_mapping.sample(trace.get("sample_id"))
    if record is None or all(v is None for v in nutrients.values()):
        return
    service = _nutrition_model()
    zero = trace.setdefault("category_zero", {})
    for key in service.NUTRIENTS:
        if nutrients.get(key) is not None or service.finite_number(record["nutrition_per_100g"].get(key)):
            continue
        rule = nutrient_category_zero.applies(record, key)
        if rule is not None:
            nutrients[key] = Decimal(0)
            zero[key] = {"rule": rule["id"], "sample_id": record["ingredient_id"]}
            continue
        estimate = lookup(record["ingredient_id"], key) if lookup else None
        if estimate is None or not estimate.usable or estimate.nutrient_per_100g is None:
            continue
        value = estimate.nutrient_per_100g * _fill_weight(grams, record, trace, cooking_factor,
                                                          weight_basis) / REFERENCE_GRAMS
        if cooking_factor is not None and record.get("state") != "cooked":
            value *= (cooking_factor.retention or {}).get(key, Decimal(1))
        nutrients[key] = value
        trace["estimated_factors"].append(estimate.marker())
        trace["estimated_nutrients"] = [*trace.get("estimated_nutrients", []), key]
        trace.setdefault("nutrient_fills", {})[key] = {"sample_id": record["ingredient_id"],
                                                       "per_100g": estimate.nutrient_per_100g,
                                                       "source": estimate.source, "status": estimate.status}
        trace["is_estimated"] = True
    if all(nutrients.get(k) is not None for k in REQUIRED_NUTRIENTS):
        trace["missing_reason"] = None


tfda_calculator = make_tfda_calculator()


def _requirements(lines, whole) -> list[IngredientRequirement]:
    """依 food_id 合併整餐需求量（保持首次出現順序），供 T13 成本計算。

    主食不計價格／缺料（decisions #16），克數未知的食材無法計算成本，兩者都不列入。
    """
    merged: dict[str, Decimal] = {}
    for ln, g in zip(lines, whole):
        if ln.role == "staple" or g is None:
            continue
        merged[ln.food_id] = merged.get(ln.food_id, Decimal(0)) + g
    return [IngredientRequirement(food_id, g) for food_id, g in merged.items()]
