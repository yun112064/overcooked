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
    """以指定克數重新計算；回傳 (每項結果, 各營養合計 Fraction 或 None, 缺值食材清單, 已知部分合計)。

    克數未知（None）的食材不送 calculator，結果列為 None，並列入每一項營養的缺值清單（decisions #53）。
    已知部分合計只加總有值的食材（沒有任何已知值時為 None）；任一食材缺值時合計為 None，不以 0 代替。
    """
    known = [i for i, g in enumerate(grams) if g is not None]
    computed = calculator([(lines[i], grams[i]) for i in known]) if known else []
    rows = [None] * len(lines)
    for i, row in zip(known, computed):
        rows[i] = row
    keys = list(REQUIRED_NUTRIENTS)
    for row in computed:
        keys += [k for k in row.nutrients if k not in keys]
    totals, missing, partial = {}, {}, {}
    for key in keys:
        values = [row.nutrients.get(key) if row is not None else None for row in rows]
        present = [Fraction(v) for v in values if v is not None]
        partial[key] = sum(present, Fraction()) if present else None
        absent = [ln.name for ln, v in zip(lines, values) if v is None]
        if absent:
            totals[key], missing[key] = None, absent
        else:
            totals[key] = partial[key]
    return rows, totals, missing, partial


def _dedupe(names: list[str]) -> list[str]:
    return list(dict.fromkeys(names))


def _partial_view(target: dict, partial: dict, missing: dict) -> None:
    """decisions #53：已知部分合計（Decimal，缺任何已知值為 None）與缺值食材名稱（去重）。"""
    target["known_partial"] = {k: (_dec(v) if v is not None else None) for k, v in partial.items()}
    target["missing_by_nutrient"] = {k: _dedupe(names) for k, names in missing.items()}


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


PORTION_PATH = PROJECT_ROOT / "config" / "portion_scaling.json"
ALIGN_CANDIDATES = 5  # 浮點搜尋前幾名逐一以 calculator 精確複核


@lru_cache(maxsize=1)
def portion_settings() -> dict:
    """decisions #48：整道縮放範圍與格點上限（config/portion_scaling.json）。"""
    import json

    data = json.loads(PORTION_PATH.read_text(encoding="utf-8"))
    return {"dish_scale_min": Decimal(data["dish_scale_min"]), "dish_scale_max": Decimal(data["dish_scale_max"]),
            "dish_scale_step": Decimal(data["dish_scale_step"]), "max_grid_points": int(data["max_grid_points"]),
            "version": data.get("version")}


def _dish_groups(lines) -> dict[str, list[int]]:
    """可整道縮放的菜：依 dish_id 分組（主食與沒有 dish_id 的列除外），保持首次出現順序。"""
    groups: dict[str, list[int]] = {}
    for i, ln in enumerate(lines):
        if ln.role != "staple" and ln.dish_id is not None:
            groups.setdefault(ln.dish_id, []).append(i)
    return groups


def _scale_values(settings: dict) -> list[Decimal]:
    lo, hi, step = settings["dish_scale_min"], settings["dish_scale_max"], settings["dish_scale_step"]
    values, v = [], lo
    while v <= hi:
        values.append(v)
        v += step
    values.append(hi)
    if lo <= 1 <= hi:
        values.append(Decimal(1))
    return sorted(set(values))


def _coarsen(values: list) -> list:
    """格點放粗：隔一個取一個，端點與 1.0（原份量）一律保留。"""
    keep = {values[0], values[-1], Decimal(1)}
    return sorted({v for j, v in enumerate(values) if j % 2 == 0 or v in keep})


def _align(lines, grams, rows, references, mode):
    """對齊搜尋（decisions #48）：每道菜整道倍率（dish_scale_min～max）＋主食在可調範圍內的克數，
    資料庫模式另含沒有 dish_id、已審核可調範圍的列（舊的逐列調整，相容保留）。

    熱量／蛋白質對克數為線性（係數取自基準計算結果），以浮點格點快速搜尋；格點總數受 max_grid_points 限制
    （先放粗逐列格點、再放粗倍率，仍超過則所有菜共用同一倍率）。回傳前幾名候選 [(克數, 各道倍率)]，
    由呼叫端以 calculator 精確（Fraction）複核 ±10%；找不到回傳空清單。
    排序：與目標的最大相對偏差 → 總移動克數 → 菜色移動克數（同分時先調主食，保持整道菜原份量）。
    """
    import heapq
    from math import prod

    settings = portion_settings()
    groups = _dish_groups(lines)
    grouped = {i for idx in groups.values() for i in idx}
    line_vars = [i for i, ln in enumerate(lines) if i not in grouped and _adjustable(ln) and grams[i] > 0
                 and (mode == "database" or ln.role == "staple")]
    if not groups and not line_vars:
        return []
    keys = REQUIRED_NUTRIENTS
    value = {i: {k: Fraction(rows[i].nutrients[k]) for k in keys} for i in range(len(lines))}
    fixed = [float(sum((value[i][k] for i in range(len(lines)) if i not in grouped and i not in line_vars),
                       Fraction())) for k in keys]
    ref = [float(references[k]) for k in keys]
    lower, upper = [float(LOWER) * r for r in ref], [float(UPPER) * r for r in ref]

    dish_axis = _scale_values(settings)
    dish_axes = {d: list(dish_axis) for d in groups}
    steps = 40
    budget = settings["max_grid_points"]

    def size():
        return prod(len(a) for a in dish_axes.values()) * (steps + 1) ** len(line_vars)

    while size() > budget:
        if line_vars and steps > 8:
            steps //= 2
        elif any(len(a) > 3 for a in dish_axes.values()):
            dish_axes = {d: _coarsen(a) for d, a in dish_axes.items()}
        elif line_vars and steps > 2:
            steps //= 2
        else:
            break
    uniform = size() > budget  # 菜太多：所有菜共用同一倍率

    axes = []  # 每個變數的選項：(標記, 值, 熱量, 蛋白質, 移動克數, 菜色移動克數)
    def dish_options(ids, values):
        totals = [float(sum((value[i][k] for i in ids), Fraction())) for k in keys]
        mass = float(sum((Fraction(grams[i]) for i in ids), Fraction()))
        return [(v, totals[0] * float(v), totals[1] * float(v), (1 - float(v)) * mass) for v in values]

    if uniform:
        axes.append([("all", v, e, p, m, m) for v, e, p, m in dish_options(sorted(grouped), dish_axis)])
    else:
        for d, idx in groups.items():
            axes.append([(d, v, e, p, m, m) for v, e, p, m in dish_options(idx, dish_axes[d])])
    for i in line_vars:
        lo, hi = Fraction(lines[i].adjustable_min), Fraction(lines[i].adjustable_max)
        points = {lo + (hi - lo) * t / steps for t in range(steps + 1)}
        if lo <= Fraction(grams[i]) <= hi:
            points.add(Fraction(grams[i]))
        per_gram = [value[i][k] / Fraction(grams[i]) for k in keys]
        axes.append([(i, g, float(per_gram[0] * g), float(per_gram[1] * g), abs(float(g - Fraction(grams[i]))), 0.0)
                     for g in sorted(points)])

    def feasible():
        for combo in itertools.product(*axes):
            e = fixed[0] + sum(o[2] for o in combo)
            pr = fixed[1] + sum(o[3] for o in combo)
            # 浮點只作篩選（含極小容差），最後一律以 calculator 精確複核
            if not (lower[0] - 1e-9 <= e <= upper[0] + 1e-9 and lower[1] - 1e-9 <= pr <= upper[1] + 1e-9):
                continue
            deviation = max(abs(e / ref[0] - 1), abs(pr / ref[1] - 1))
            key = (round(deviation, 9), round(sum(o[4] for o in combo), 6), round(sum(o[5] for o in combo), 6))
            yield key, combo

    out = []
    for _, combo in heapq.nsmallest(ALIGN_CANDIDATES, feasible(), key=lambda item: item[0]):
        new, scales = list(grams), {d: Decimal(1) for d in groups}
        for option in combo:
            tag, v = option[0], option[1]
            if tag == "all":
                scales = {d: v for d in groups}
            elif tag in groups:
                scales[tag] = v
            else:
                new[tag] = _dec(v)
        for d, idx in groups.items():
            for i in idx:
                new[i] = grams[i] * scales[d]
        out.append((new, scales))
    return out


def calculate_meal(lines: list[IngredientLine], targets: MealTargets, *, mode: str,
                   calculator: Calculator) -> dict:
    """驗證整餐並彙整「LLM 推估」標示（decisions #19）；計算本體見 _calculate_meal。"""
    result = _calculate_meal(lines, targets, mode=mode, calculator=calculator)
    for ln, row in zip(lines, result["ingredients"]):
        # decisions #48：食譜原每人份克數與該道採用的整道倍率（主食與未分組的列為 None）
        row["recipe_grams_per_n"] = ln.grams_per_n
        row["dish_scale"] = result["dish_scales"].get(ln.dish_id) if ln.role != "staple" else None
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
                     "energy_kcal": None, "protein_g": None, "within_range": False, "adjusted": False,
                     "known_partial": {}, "missing_by_nutrient": {}},
        "final": {"target": {k: _dec(v) for k, v in final_targets.items()},
                  "energy_kcal": None, "protein_g": None, "gap": {}, "within_range": False,
                  "other_nutrients": {}, "missing_nutrients": {}, "known_partial": {}, "missing_by_nutrient": {}},
        "ingredients": [], "requirements": [], "unresolved_ingredients": [],
        # decisions #48：每道菜（dish_id）採用的整道倍率；未對齊時為 1
        "dish_scales": {d: Decimal(1) for d in _dish_groups(lines)},
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
    rows, totals, missing, partial = _evaluate(lines, grams, calculator)
    _partial_view(result["baseline"], partial, missing)
    incomplete = [k for k in REQUIRED_NUTRIENTS if totals[k] is None]
    # 克數已解析、但營養缺值的食材（克數未解析者已由 INGREDIENT_UNRESOLVED 說明）
    lacking = [(ln, r) for ln, r in zip(lines, rows)
               if r is not None and any(r.nutrients.get(k) is None for k in REQUIRED_NUTRIENTS)]
    if lacking:
        nutrients = [k for k in incomplete if any(r.nutrients.get(k) is None for _, r in lacking)]
        result["block_reasons"].append({"code": "NUTRITION_INCOMPLETE", "nutrients": nutrients,
                                        "ingredients": {k: [ln.name for ln, r in lacking if r.nutrients.get(k) is None]
                                                        for k in nutrients},
                                        "errors": {ln.name: r.error for ln, r in lacking if r.error}})
        result["suggestions"].append({
            "code": "NUTRITION_INCOMPLETE", "nutrients": nutrients,
            "action": "補齊這些食材的 TFDA 營養資料（或改用有樣品的食材）後重新計算",
            "ingredients": [{"food_id": ln.food_id, "name": ln.name, "error": r.error} for ln, r in lacking]})
    if unresolved or incomplete:
        # decisions #53：不再整個清空——已知食材照算整餐用量與已知部分合計，缺值食材列名；合計仍為 None
        _unverified_final(result, lines, grams, n, calculator, final_targets)
        return result

    if not all(_within(totals[k], references[k]) for k in REQUIRED_NUTRIENTS):
        # decisions #48：整道縮放＋主食（資料庫與 AI 模式皆可）；資料庫模式另含舊的逐列可調範圍
        for aligned, scales in _align(lines, grams, rows, references, mode):
            check_rows, check_totals, check_missing, check_partial = _evaluate(lines, aligned, calculator)
            if all(check_totals[k] is not None and _within(check_totals[k], references[k])
                   for k in REQUIRED_NUTRIENTS):
                grams, rows, totals = aligned, check_rows, check_totals
                _partial_view(result["baseline"], check_partial, check_missing)
                result["baseline"]["adjusted"] = True
                result["dish_scales"] = scales
                break
    result["baseline"].update(energy_kcal=_dec(totals["energy_kcal"]), protein_g=_dec(totals["protein_g"]))
    baseline_reasons = _range_reasons("baseline", totals, references)
    if baseline_reasons:
        # 無可行方案：不移動目標或容差，只回報差距、可調食材與調整建議
        result["block_reasons"] += baseline_reasons
        result["block_reasons"].append({
            "code": "NO_FEASIBLE_BASELINE", "mode": mode,
            "adjustable_food_ids": [ln.food_id for ln in lines
                                    if _adjustable(ln) and (mode == "database" or ln.role == "staple")],
            "adjustable_dish_ids": list(result["dish_scales"])})
        result["suggestions"] = _range_suggestions(lines, grams, rows, totals, references, mode)
        _unverified_final(result, lines, grams, n, calculator, final_targets)
        return result
    result["baseline"]["within_range"] = True

    whole = [g * n for g in grams]
    final_rows, final_totals, final_missing, final_partial = _evaluate(lines, whole, calculator)
    result["ingredients"] = [_row(ln, grams[i], whole[i], final_rows[i], n) for i, ln in enumerate(lines)]
    result["requirements"] = _requirements(lines, whole)
    final = result["final"]
    final["missing_nutrients"] = final_missing
    _partial_view(final, final_partial, final_missing)
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
    """不可採用：仍以整餐用量重新計算並列出（未驗證），供查看與成本計算。克數未知的食材整餐量為 None，
    列入缺值清單；已知部分合計只加總已知食材（decisions #53）。"""
    whole = [g * n if g is not None else None for g in grams]
    rows, totals, missing, partial = _evaluate(lines, whole, calculator)
    result["ingredients"] = [_row(ln, grams[i], whole[i], rows[i], n) for i, ln in enumerate(lines)]
    result["requirements"] = _requirements(lines, whole)
    final = result["final"]
    final["missing_nutrients"] = missing
    _partial_view(final, partial, missing)
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
            approved = _adjustable(ln) and (mode == "database" or ln.role == "staple") and not (
                ln.role != "staple" and ln.dish_id is not None)
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
        # decisions #48：整道縮放（倍率 dish_scale_min～max；目前為 1）在該營養上最多能補多少
        settings, dishes = portion_settings(), []
        for dish_id, idx in _dish_groups(lines).items():
            amount = sum((Fraction(rows[i].nutrients.get(key) or 0) for i in idx), Fraction())
            bound = settings["dish_scale_max"] if increase else settings["dish_scale_min"]
            change = max(Fraction(), amount * (Fraction(bound) - 1) if increase else amount * (1 - Fraction(bound)))
            reachable += change
            dishes.append({"dish_id": dish_id, "direction": "increase" if increase else "decrease",
                           "scale_min": settings["dish_scale_min"], "scale_max": settings["dish_scale_max"],
                           "max_change": _dec(change)})
        out.append({"code": "NUTRITION_OUT_OF_RANGE", "nutrient": key,
                    "direction": "increase" if increase else "decrease",
                    "value": _dec(total), "lower": _dec(lower), "upper": _dec(upper), "needed_change": _dec(needed),
                    "within_adjustable_ranges": reachable >= needed,
                    "ingredients": [item for _, item in items[:5]], "dishes": dishes})
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
# decisions #51：市售即為熟品的食材（config/market_cooked_ingredients.json）＋TFDA 熟樣本 → 熟重
MARKET_COOKED_SAMPLE = "market_cooked_cooked_sample"
MARKET_COOKED_PATH = PROJECT_ROOT / "config" / "market_cooked_ingredients.json"
# decisions #49(a)：unspecified 的調味料、糖、油脂、香料、乾貨等以原樣組成計算（config/nutrition_as_is_categories.json）
UNSPECIFIED_AS_IS = "unspecified_as_is"
AS_IS_PATH = PROJECT_ROOT / "config" / "nutrition_as_is_categories.json"
# decisions #49(b)：生樣本查不到烹調係數 → TFDA 生組成 × 生重近似（熱量、蛋白質也適用）
RAW_COMPOSITION_FALLBACK = "raw_composition_fallback"


@lru_cache(maxsize=1)
def as_is_rules() -> tuple[dict, ...]:
    import json

    return tuple(json.loads(AS_IS_PATH.read_text(encoding="utf-8"))["rules"])


def as_is_rule(record: dict | None) -> dict | None:
    """TFDA 樣本為 unspecified 且類別碼符合設定時回傳規則（id、reason…），否則 None。"""
    if not record or record.get("state") != "unspecified":
        return None
    sample_id = record.get("ingredient_id") or ""
    return next((r for r in as_is_rules() if any(sample_id.startswith(p) for p in r["prefixes"])), None)


@lru_cache(maxsize=1)
def market_cooked_entries() -> tuple[dict, ...]:
    import json

    return tuple(json.loads(MARKET_COOKED_PATH.read_text(encoding="utf-8"))["ingredients"])


def is_market_cooked(food_id: str | None, name: str | None) -> bool:
    name = (name or "").strip()
    return any((food_id and e.get("food_id") == food_id) or (name and e.get("name") == name)
               for e in market_cooked_entries())


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
    - 克數來源順序（decisions #20／#55）：原文 g 克數 → 工作簿該列 weight_g（source_weight_g，g 單位範圍列即工作簿中點，
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
        if weight is None and row.source_weight_g is not None:  # 含 g 單位範圍列（decisions #55）
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


# decisions #49(c)：常見同義寫法 → 食譜工作簿用語（炒、拌、煮、煎、燙、蒸、炸、烤、燉、滷、氣炸、微波），
# 再交給 T04 正規化；工作簿用語本身不在此表，既有快取鍵不變
METHOD_SYNONYMS = {
    "清炒": "炒", "快炒": "炒", "爆炒": "炒", "熱炒": "炒", "翻炒": "炒", "乾炒": "炒", "拌炒": "炒",
    "水煮": "煮", "燉湯": "煮", "熬煮": "煮", "熬": "煮", "煨": "燉", "燜": "燉", "紅燒": "燉", "燉煮": "燉",
    "清蒸": "蒸", "蒸煮": "蒸", "隔水蒸": "蒸",
    "香煎": "煎", "乾煎": "煎", "煎炒": "煎", "半煎炸": "煎",
    "油炸": "炸", "酥炸": "炸", "乾炸": "炸", "油煎炸": "炸", "炸物": "炸",
    "汆燙": "燙", "氽燙": "燙", "川燙": "燙", "燙煮": "燙", "涮": "燙", "汆": "燙",
    "涼拌": "拌", "拌勻": "拌", "生拌": "拌",
    "烘烤": "烤", "焗": "烤", "烘": "烤", "焗烤": "烤", "炙烤": "烤", "烤箱": "烤",
    "滷製": "滷", "滷煮": "滷", "紅滷": "滷",
    "氣炸鍋": "氣炸", "微波爐": "微波",
}
METHOD_SEPARATORS = ("+", "、", "，", ",", "/", "／", "及", "和", "與", "後", "再", " ")


def normalize_cooking_method(method: str | None) -> str | None:
    """單一烹調方式正規化：同義寫法先轉成工作簿用語，再沿用 T04 cooking_yield_service 的對照。"""
    if not method or not str(method).strip():
        return None
    text = str(method).strip()
    text = METHOD_SYNONYMS.get(text, text)
    _nutrition_model()
    import cooking_yield_service

    return cooking_yield_service.normalize_method(text) or None


def combined_method_key(methods) -> str | None:
    """多種烹調方式的組合鍵：各方式正規化、去重、排序後以「+」連接（批次推估與查表共用）。"""
    keys = sorted({normalize_cooking_method(m) for m in methods or () if m and str(m).strip()} - {None})
    return "+".join(keys) or None


def cooking_method_key(methods) -> str | None:
    """decisions #49(c)：AI 路徑用的烹調方式鍵，與快取組合鍵（combined_method_key）同一規則。

    methods 可為單一字串（「炒」「清炒」「煮+炒」「炒、拌」皆可，依 METHOD_SEPARATORS 拆開）或字串清單；
    回傳正規化、去重、排序後以「+」連接的鍵（單一方式即該方式，如 "stir"），沒有方式回傳 None。
    """
    if methods is None:
        return None
    parts = [methods] if isinstance(methods, str) else [str(m) for m in methods if m is not None]
    for sep in METHOD_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(sep)]
    return combined_method_key([p for p in parts if p.strip()])


def _method_key(ln: IngredientLine) -> str | None:
    if ln.cooking_method:
        return cooking_method_key(ln.cooking_method)
    return cooking_method_key(ln.cooking_methods) if ln.cooking_methods else None


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
                         tfda_resolver=None, nutrient_fills: NutrientFillLookup | None = None,
                         raw_fallback: bool = True) -> Calculator:
    """建立 TFDA calculator；cooking_factors(food_id, 正規化烹調方式) 回傳快取係數或 None。

    tfda_resolver（decisions #28，app.services.tfda_mapping.TfdaResolver）：食譜名稱 → TFDA 樣本；
    未提供時只用精確名稱（T04 原行為）。
    nutrient_fills(TFDA 樣本 ID, 營養素)（decisions #32／#45）：TFDA 樣本 7 種營養素欄位空白時的快取推估（每 100 g）；
    符合「類別必然為 0」規則（config/nutrient_category_zero.json）者一律先補已知 0，不查推估（見 _fill_blanks）。

    優先順序（decisions #19／#32）：TFDA／USDA 實測 > 人工核可／覆寫 > LLM 推估；rejected 永不使用。
    人工與 LLM 的先後由查表端（app.estimation.store）決定，此處只在 T04 實測算不出必要營養時才套用。
    raw_fallback（decisions #49(b)）：查不到烹調係數時以 TFDA 生組成 × 生重近似；推估批次掃描缺口時關閉
    （才看得出哪些組合仍值得推估係數）。
    """
    def calculate(rows: list[tuple[IngredientLine, Decimal]]) -> list[IngredientNutrition]:
        return _tfda_calculate(rows, cooking_factors, tfda_resolver, nutrient_fills, raw_fallback)
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


def _effective_basis(ln: IngredientLine, record: dict | None) -> tuple[str, str | None, str | None]:
    """(weight_basis, 依據, 烹調方式)。對照到 TFDA 熟樣本時：

    - decisions #35／#51：工作簿慣例（#17 生重）或 AI 宣告（MODEL_DECLARED）的生重，食材名稱本身是熟食（白飯、
      白米飯…）或列在市售熟品清單（桂竹筍、鹹蛋黃、杏仁果…）時改視為熟重；
    - 熟重配熟樣本一律不再套烹調方式（熟樣本已是熟的狀態；另列的油鹽照算）。
    """
    cooked_sample = bool(record and record.get("state") == "cooked")
    if (cooked_sample and ln.weight_basis == "raw"
            and ln.weight_basis_source in (WORKBOOK_RAW_CONVENTION, MODEL_DECLARED)):
        if is_market_cooked(ln.food_id, ln.name):
            return "cooked", MARKET_COOKED_SAMPLE, None
        if is_cooked_food_name(ln.name):
            return "cooked", COOKED_FOOD_SAMPLE, None
    if cooked_sample and ln.weight_basis == "cooked":
        return "cooked", ln.weight_basis_source, None
    return ln.weight_basis, ln.weight_basis_source, ln.cooking_method


def _tfda_calculate(rows, cooking_factors, tfda_resolver=None, nutrient_fills=None,
                    raw_fallback=True) -> list[IngredientNutrition]:
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
    records = [tfda_mapping.sample(res.sample_id) if res else None for res in resolutions]
    bases = [_effective_basis(ln, record) for (ln, _), record in zip(rows, records)]
    # decisions #49(a)：unspecified 的調味料、糖、油脂、香料、乾貨等一律以原樣組成計算（生重、不套烹調）
    as_is = [as_is_rule(record) if basis == "raw" else None for record, (basis, _, _) in zip(records, bases)]
    items = []
    for (ln, _), res, (basis, _, method), w, rule in zip(rows, resolutions, bases, water, as_is):
        if w:
            continue
        item = {"name": res.tfda_name if res else ln.name, "weight_g": float(REFERENCE_GRAMS),
                "weight_basis": basis, "consumed_as_is": ln.consumed_as_is or rule is not None}
        if method and rule is None:
            item["cooking_method"] = method
        items.append(item)
    recipe = service.calculate_recipe_nutrition("meal", items, estimator=_no_request_time_llm) if items else {}
    t04_rows = iter(recipe.get("ingredients") or [{}] * len(items))
    out = []
    for (ln, grams), res, record, (basis, basis_source, method), w, rule in zip(
            rows, resolutions, records, bases, water, as_is):
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
        method_key = _method_key(ln)  # 熟食熟重（#35／#51）時 basis 為 cooked，不套烹調係數
        if rule is not None and row.get("success"):
            trace.update(nutrition_basis=UNSPECIFIED_AS_IS, as_is_rule=rule["id"])
        # T04 烹調管線不接受 unspecified 狀態的樣本（回報 ingredient_not_found）；有生組成時仍可套快取係數
        usable_row = row.get("success") or (row.get("error") == "ingredient_not_found"
                                            and _raw_composition(service, tfda_name) is not None)
        applied, factor, fallback = False, None, False
        if (cooking_factors and method_key and basis == "raw" and usable_row and rule is None
                and any(nutrients.get(k) is None for k in REQUIRED_NUTRIENTS)):
            factor = cooking_factors(ln.food_id, method_key)
            if factor is not None and factor.usable:
                _apply_cooking_factor(service, ln, grams, factor, nutrients, trace, tfda_name)
                applied = bool(trace["estimated_factors"])
        if (raw_fallback and basis == "raw" and usable_row and rule is None and record is not None
                and record.get("state") != "cooked" and any(nutrients.get(k) is None for k in REQUIRED_NUTRIENTS)):
            fallback = _raw_fallback(service, record, grams, nutrients, trace)
        if row.get("success") or applied or fallback:
            _fill_blanks(nutrient_fills, grams, nutrients, trace, factor if applied else None, basis)
        error = (None if applied or fallback else row.get("error")) or (
            None if row.get("success") or applied or fallback else "nutrition_unavailable")
        if (error is None and basis == "cooked" and record is not None and record.get("state") != "cooked"
                and all(nutrients.get(k) is None for k in REQUIRED_NUTRIENTS)):
            # 不靜默變成未知：熟重（例如 AI 宣告 cooked）配生樣本沒有依據換回生重
            error = "cooked_weight_requires_cooked_sample"
        out.append(IngredientNutrition(nutrients, trace, error))
    return out


def _raw_fallback(service, record: dict, grams: Decimal, nutrients: dict, trace: dict) -> bool:
    """decisions #49(b)：查不到烹調係數（多方式組合鍵、AI 單一方式或沒有方式）時，以 TFDA 生組成 × 生重近似
    尚缺的營養（熱量、蛋白質也適用，延伸 #40），trace 標 raw_composition_fallback；畫面不顯示（#42）。
    TFDA 生組成空白者仍為 None（之後交給類別 0 規則或 nutrient_fill）。"""
    filled = []
    for key in service.NUTRIENTS:
        value = record["nutrition_per_100g"].get(key)
        if nutrients.get(key) is None and service.finite_number(value):
            nutrients[key] = Decimal(str(value)) * grams / REFERENCE_GRAMS
            filled.append(key)
    if not filled:
        return False
    trace["raw_composition_fallback"] = True
    trace["nutrition_basis"] = RAW_COMPOSITION_FALLBACK
    trace["approximate_nutrients"] = [*trace.get("approximate_nutrients", []), *filled]
    trace.setdefault("nutrient_basis", {}).update({k: RAW_COMPOSITION_FALLBACK for k in filled})
    if trace.get("sample_id") is None:
        source = record["source"]
        trace.update(sample_id=source.get("source_id"), provider=source.get("provider"),
                     source_version=source.get("source_version"), source_url=source.get("source_url"))
    if all(nutrients.get(k) is not None for k in REQUIRED_NUTRIENTS):
        trace["missing_reason"] = None
    return True


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
