"""Exact T12 calculations; the TFDA and cooking evidence are supplied explicitly."""
from __future__ import annotations

import json
from itertools import product
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal

from .targets import MealTargets, PlanningInputError, REQUIRED_TARGETS, exact

Number = str | int | Decimal | Fraction
Mode = Literal["database_menu", "ai_generated_menu"]


@dataclass(frozen=True)
class CookingEvidence:
    evidence_id: str
    sample_id: str
    cooking_method: str
    edible_fraction: Number | None = None
    yield_factor: Number | None = None
    retention: Mapping[str, Number | None] = field(default_factory=dict)
    source: str | None = None
    source_version: str | None = None


@dataclass(frozen=True)
class IngredientQuantity:
    line_id: str
    food_id: str
    sample_id: str
    amount_g: Number
    weight_basis: Literal["purchase", "raw", "cooked"]
    consumed_as_is: bool
    cooking_step_g: Number | None
    cooking_method: str | None = None
    evidence: CookingEvidence | None = None
    is_required: bool = True
    adjustment_status: str = "not_reviewed"
    adjustable_min_g: Number | None = None
    adjustable_max_g: Number | None = None
    original_servings: Number | None = None


@dataclass(frozen=True)
class RecipeCandidate:
    mode: Mode
    ingredients: tuple[IngredientQuantity, ...]
    recipe_ids: tuple[str, ...] = ()
    review_status: str = "not_reviewed"


@dataclass(frozen=True)
class NutritionPolicy:
    catalog_path: Path
    sample_mappings: Mapping[str, str]
    required_nutrients: tuple[str, ...]
    approved_evidence: Mapping[str, CookingEvidence] = field(default_factory=dict)
    approved_for_adoption: bool = False


@dataclass(frozen=True)
class NutritionEstimate:
    totals: Mapping[str, Fraction | None]
    missing: tuple[str, ...]
    sources: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class ToleranceResult:
    passed: bool
    bounds: Mapping[str, tuple[Fraction, Fraction]]
    difference: Mapping[str, Fraction | None]


@dataclass(frozen=True)
class AlignmentResult:
    recipe: RecipeCandidate | None
    estimate: NutritionEstimate
    check: ToleranceResult
    reason: str | None = None


@dataclass(frozen=True)
class MealValidationResult:
    baseline_check: ToleranceResult
    final_recipe: RecipeCandidate | None
    final_estimate: NutritionEstimate | None
    final_check: ToleranceResult | None
    nutrition_passed: bool
    adoption_blockers: tuple[str, ...]
    reason: str | None = None


def _approved_evidence(line: IngredientQuantity, policy: NutritionPolicy) -> CookingEvidence | None:
    ev = line.evidence
    if ev is None:
        return None
    approved = policy.approved_evidence.get(ev.evidence_id)
    if (approved is None or approved.evidence_id != ev.evidence_id
            or approved.sample_id != line.sample_id or approved.cooking_method != line.cooking_method
            or not approved.source or not approved.source_version):
        return None
    return approved


def calculate_nutrition(recipe: RecipeCandidate, *, policy: NutritionPolicy) -> NutritionEstimate:
    if recipe.mode not in ("database_menu", "ai_generated_menu") or not recipe.ingredients:
        raise PlanningInputError("A mode and nonempty ingredient list are required.")
    if not set(REQUIRED_TARGETS).issubset(policy.required_nutrients):
        raise PlanningInputError("The policy must include energy and protein.")
    try:
        with policy.catalog_path.open(encoding="utf-8") as stream:
            data = json.load(stream, parse_float=Decimal)
        catalog = {item["ingredient_id"]: item for item in data}
    except (OSError, ValueError, TypeError, KeyError):
        return NutritionEstimate({k: None for k in policy.required_nutrients}, ("tfda_catalog_unavailable",), ())
    totals: dict[str, Fraction | None] = {k: Fraction(0) for k in policy.required_nutrients}
    missing: list[str] = []
    sources: list[Mapping[str, object]] = []
    seen: set[str] = set()
    for line in recipe.ingredients:
        if not line.line_id or line.line_id in seen:
            raise PlanningInputError("Ingredient line identifiers must be unique.")
        seen.add(line.line_id)
        if type(line.consumed_as_is) is not bool or type(line.is_required) is not bool:
            missing.append(f"{line.line_id}:invalid_boolean")
            for key in totals:
                totals[key] = None
            continue
        amount = exact(line.amount_g)
        if amount < 0 or (line.is_required and amount == 0):
            raise PlanningInputError("Required ingredients must have positive amounts.")
        record = catalog.get(line.sample_id)
        if (not record or policy.sample_mappings.get(line.food_id) != line.sample_id
                or record.get("source", {}).get("provider") != "TFDA"
                or record.get("source", {}).get("source_id") != line.sample_id
                or not record.get("source", {}).get("source_version")):
            missing.append(f"{line.line_id}:tfda_mapping_or_source")
            for k in totals:
                totals[k] = None
            continue
        ev = _approved_evidence(line, policy)
        state = record.get("state")
        raw_weight = cooked_weight = purchase_weight = None
        if line.weight_basis == "purchase":
            purchase_weight = amount
            if ev and ev.edible_fraction is not None and 0 < exact(ev.edible_fraction) <= 1:
                raw_weight = amount * exact(ev.edible_fraction)
        elif line.weight_basis == "raw":
            raw_weight = amount
        elif line.weight_basis == "cooked":
            cooked_weight = amount
        else:
            raise PlanningInputError("Unknown weight basis.")
        if state == "cooked" and cooked_weight is None and raw_weight is not None:
            if ev and ev.yield_factor is not None and exact(ev.yield_factor) > 0:
                cooked_weight = raw_weight * exact(ev.yield_factor)
        if state == "raw" and raw_weight is None and cooked_weight is not None:
            if ev and ev.yield_factor is not None and exact(ev.yield_factor) > 0:
                raw_weight = cooked_weight / exact(ev.yield_factor)
        valid_state = state in ("raw", "cooked")
        is_direct = line.consumed_as_is and line.cooking_method is None
        if state == "cooked":
            usable_weight = cooked_weight
            valid_cooking = is_direct or (ev is not None and bool(line.cooking_method))
        else:
            usable_weight = raw_weight
            valid_cooking = is_direct or (ev is not None and bool(line.cooking_method))
        sources.append({"line_id": line.line_id, "food_id": line.food_id, "sample_id": line.sample_id,
                        "source": deepcopy(record["source"]), "sample_state": state,
                        "nutrition_per_100g": deepcopy(record.get("nutrition_per_100g", {})),
                        "purchase_weight_g": purchase_weight, "raw_edible_weight_g": raw_weight,
                        "cooked_edible_weight_g": cooked_weight,
                        "evidence_id": ev.evidence_id if ev else None,
                        "cooking_evidence": asdict(ev) if ev else None})
        for key in totals:
            value = record.get("nutrition_per_100g", {}).get(key)
            retention: Fraction | None = Fraction(1) if state == "cooked" or is_direct else None
            if state == "raw" and not is_direct and ev and ev.retention.get(key) is not None:
                retention = exact(ev.retention[key])
                if retention < 0 or retention > 1:
                    retention = None
            if not valid_state or not valid_cooking or usable_weight is None or value is None or retention is None:
                missing.append(f"{line.line_id}:{key}:nutrition_or_conversion")
                totals[key] = None
                continue
            amount_value = exact(value)
            if amount_value < 0:
                missing.append(f"{line.line_id}:{key}:invalid_tfda_value")
                totals[key] = None
                continue
            previous = totals[key]
            if previous is not None:
                totals[key] = previous + amount_value * usable_weight * retention / 100
    return NutritionEstimate(totals, tuple(missing), tuple(sources))


def check_tolerance(actual: NutritionEstimate, expected: Mapping[str, Fraction]) -> ToleranceResult:
    bounds = {k: (exact(expected[k]) * Fraction(9, 10), exact(expected[k]) * Fraction(11, 10))
              for k in REQUIRED_TARGETS}
    values = {k: actual.totals.get(k) for k in REQUIRED_TARGETS}
    difference = {k: (value - exact(expected[k]) if value is not None else None)
                  for k, value in values.items()}
    passed = not actual.missing and all(value is not None and bounds[k][0] <= value <= bounds[k][1]
                                       for k, value in values.items())
    return ToleranceResult(passed, bounds, difference)


def align_baseline(recipe: RecipeCandidate, targets: MealTargets, *, policy: NutritionPolicy) -> AlignmentResult:
    normalized = []
    for line in recipe.ingredients:
        if line.original_servings is None:
            missing = NutritionEstimate({k: None for k in policy.required_nutrients},
                                        (f"{line.line_id}:missing_original_servings",), ())
            return AlignmentResult(None, missing, check_tolerance(missing, targets.reference),
                                   "missing_original_servings")
        servings = exact(line.original_servings)
        if servings <= 0:
            raise PlanningInputError("Original servings must be positive.")
        normalized.append(replace(line, amount_g=exact(line.amount_g) / servings,
            adjustable_min_g=(exact(line.adjustable_min_g) / servings if line.adjustable_min_g is not None else None),
            adjustable_max_g=(exact(line.adjustable_max_g) / servings if line.adjustable_max_g is not None else None),
            original_servings=1))
    recipe = replace(recipe, ingredients=tuple(normalized))
    estimate = calculate_nutrition(recipe, policy=policy)
    check = check_tolerance(estimate, targets.reference)
    if estimate.missing:
        return AlignmentResult(None, estimate, check, "missing_nutrition_or_conversion")
    if recipe.mode == "database_menu" and (recipe.review_status != "approved" or not recipe.recipe_ids):
        return AlignmentResult(None, estimate, check, "recipe_not_reviewed")
    reviewed_bounds: dict[str, tuple[Fraction, Fraction]] = {}
    if recipe.mode == "database_menu":
        for line in recipe.ingredients:
            if line.adjustment_status == "approved":
                if line.adjustable_min_g is None or line.adjustable_max_g is None:
                    return AlignmentResult(None, estimate, check, "missing_reviewed_range")
                lower, upper, current = exact(line.adjustable_min_g), exact(line.adjustable_max_g), exact(line.amount_g)
                if lower < 0 or lower > upper or not lower <= current <= upper or (line.is_required and lower == 0):
                    return AlignmentResult(None, estimate, check, "invalid_reviewed_range")
                reviewed_bounds[line.line_id] = (lower, upper)
    if check.passed:
        return AlignmentResult(recipe, estimate, check)
    if recipe.mode == "database_menu":
        ranges: list[tuple[Fraction, Fraction]] = []
        coefficients: list[tuple[Fraction, Fraction]] = []
        for line in recipe.ingredients:
            amount = exact(line.amount_g)
            if line.adjustment_status == "approved":
                lower, upper = reviewed_bounds[line.line_id]
            else:
                lower = upper = amount
            unit = replace(line, amount_g=Fraction(1))
            one = calculate_nutrition(replace(recipe, ingredients=(unit,)), policy=policy)
            energy, protein = one.totals.get("energy_kcal"), one.totals.get("protein_g")
            if one.missing or energy is None or protein is None:
                return AlignmentResult(None, estimate, check, "missing_nutrition_or_conversion")
            ranges.append((lower, upper))
            coefficients.append((energy, protein))
        # The attainable (energy, protein) region is the Minkowski sum of the
        # per-ingredient line segments. Keep the weights at each polygon vertex.
        points: list[tuple[Fraction, Fraction, tuple[Fraction, ...]]] = [(Fraction(0), Fraction(0), ())]
        for (low, high), (energy, protein) in zip(ranges, coefficients):
            points = _hull([(x + energy * amount, y + protein * amount, weights + (amount,))
                            for x, y, weights in points for amount in (low, high)])
        for axis, bound, is_lower in ((0, check.bounds["energy_kcal"][0], True),
                                      (0, check.bounds["energy_kcal"][1], False),
                                      (1, check.bounds["protein_g"][0], True),
                                      (1, check.bounds["protein_g"][1], False)):
            points = _clip(points, axis, bound, is_lower)
            if not points:
                break
        if points:
            weights = points[0][2]
            adjusted = replace(recipe, ingredients=tuple(replace(line, amount_g=weight)
                                                       for line, weight in zip(recipe.ingredients, weights)))
            recalculated = calculate_nutrition(adjusted, policy=policy)
            adjusted_check = check_tolerance(recalculated, targets.reference)
            if adjusted_check.passed:
                return AlignmentResult(adjusted, recalculated, adjusted_check)
    return AlignmentResult(None, estimate, check, "no_feasible_solution")


def _hull(points: list[tuple[Fraction, Fraction, tuple[Fraction, ...]]]) -> list[tuple[Fraction, Fraction, tuple[Fraction, ...]]]:
    unique = {(p[0], p[1]): p for p in points}
    ordered = [unique[k] for k in sorted(unique)]
    if len(ordered) <= 1:
        return ordered
    def cross(a: tuple, b: tuple, c: tuple) -> Fraction:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    def side(vertices: list) -> list:
        result: list = []
        for point in vertices:
            while len(result) >= 2 and cross(result[-2], result[-1], point) <= 0:
                result.pop()
            result.append(point)
        return result
    return side(ordered)[:-1] + side(list(reversed(ordered)))[:-1]


def _clip(points: list[tuple[Fraction, Fraction, tuple[Fraction, ...]]], axis: int,
          bound: Fraction, is_lower: bool) -> list[tuple[Fraction, Fraction, tuple[Fraction, ...]]]:
    if not points:
        return []
    def inside(p: tuple) -> bool:
        return p[axis] >= bound if is_lower else p[axis] <= bound
    output = []
    previous = points[-1]
    for point in points:
        if inside(previous) != inside(point):
            previous_coordinate = previous[0] if axis == 0 else previous[1]
            point_coordinate = point[0] if axis == 0 else point[1]
            factor = (bound - previous_coordinate) / (point_coordinate - previous_coordinate)
            output.append((previous[0] + factor * (point[0] - previous[0]),
                           previous[1] + factor * (point[1] - previous[1]),
                           tuple(a + factor * (b - a) for a, b in zip(previous[2], point[2]))))
        if inside(point):
            output.append(point)
        previous = point
    return output


def scale_to_cooking_units(baseline: RecipeCandidate, total_n: Fraction) -> RecipeCandidate:
    total_n = exact(total_n)
    if total_n <= 0:
        raise PlanningInputError("Total N must be positive.")
    rows = []
    for line in baseline.ingredients:
        if line.cooking_step_g is None or exact(line.cooking_step_g) <= 0:
            raise PlanningInputError(f"{line.line_id}: cooking unit is missing.")
        step = exact(line.cooking_step_g)
        value = exact(line.amount_g) * total_n / step
        rounded_units = (2 * value.numerator + value.denominator) // (2 * value.denominator)
        if line.is_required and rounded_units == 0:
            raise PlanningInputError(f"{line.line_id}: required ingredient rounds to zero.")
        rows.append(replace(line, amount_g=rounded_units * step))
    return replace(baseline, ingredients=tuple(rows))


def _reviewed_quantities_hold(baseline: RecipeCandidate, final: RecipeCandidate, total_n: Fraction) -> bool:
    for base, cooked in zip(baseline.ingredients, final.ingredients):
        normalized = exact(cooked.amount_g) / total_n
        if base.adjustment_status == "approved":
            if base.adjustable_min_g is None or base.adjustable_max_g is None:
                return False
            if not exact(base.adjustable_min_g) <= normalized <= exact(base.adjustable_max_g):
                return False
        elif normalized != exact(base.amount_g):
            return False
    return True


def _search_cookable(baseline: RecipeCandidate, total_n: Fraction, targets: MealTargets,
                     policy: NutritionPolicy) -> tuple[RecipeCandidate | None, str]:
    choices: list[list[Fraction]] = []
    coefficients: list[tuple[Fraction, Fraction]] = []
    combinations = 1
    for line in baseline.ingredients:
        if line.cooking_step_g is None or exact(line.cooking_step_g) <= 0:
            return None, "missing_cooking_unit"
        step = exact(line.cooking_step_g)
        if line.adjustment_status == "approved":
            if line.adjustable_min_g is None or line.adjustable_max_g is None:
                return None, "missing_reviewed_range"
            low = exact(line.adjustable_min_g) * total_n / step
            high = exact(line.adjustable_max_g) * total_n / step
            low_count = -(-low.numerator // low.denominator)
            high_count = high.numerator // high.denominator
            counts = range(max(1 if line.is_required else 0, low_count), high_count + 1)
            if not counts:
                return None, "no_feasible_cooking_units"
            combinations *= len(counts)
            if combinations > 4096:
                return None, "cooking_unit_search_limit_reached"
            preferred = exact(line.amount_g) * total_n
            choices.append(sorted((count * step for count in counts), key=lambda value: abs(value - preferred)))
        else:
            scaled = exact(line.amount_g) * total_n
            if (scaled / step).denominator != 1:
                return None, "no_feasible_cooking_units"
            choices.append([scaled])
        one = calculate_nutrition(replace(baseline, ingredients=(replace(line, amount_g=1),)), policy=policy)
        energy, protein = one.totals.get("energy_kcal"), one.totals.get("protein_g")
        if one.missing or energy is None or protein is None:
            return None, "missing_nutrition_or_conversion"
        coefficients.append((energy, protein))
    for quantities in product(*choices):
        estimate = NutritionEstimate({"energy_kcal": sum((q * c[0] for q, c in zip(quantities, coefficients)), Fraction()),
                                      "protein_g": sum((q * c[1] for q, c in zip(quantities, coefficients)), Fraction())}, (), ())
        if check_tolerance(estimate, targets.target).passed:
            candidate = replace(baseline, ingredients=tuple(replace(line, amount_g=amount)
                                for line, amount in zip(baseline.ingredients, quantities)))
            return candidate, ""
    return None, "no_feasible_cooking_units"


def validate_meal(recipe: RecipeCandidate, targets: MealTargets, *, policy: NutritionPolicy) -> MealValidationResult:
    alignment = align_baseline(recipe, targets, policy=policy)
    blockers: list[str] = []
    if not targets.approved_for_adoption:
        blockers.append("target_rules_not_approved")
    if not policy.approved_for_adoption:
        blockers.append("nutrition_policy_not_approved")
    if alignment.recipe is None:
        return MealValidationResult(alignment.check, None, None, None, False, tuple(blockers), alignment.reason)
    try:
        final_recipe = scale_to_cooking_units(alignment.recipe, targets.total_n)
    except PlanningInputError:
        return MealValidationResult(alignment.check, None, None, None, False, tuple(blockers), "missing_cooking_unit")
    final = calculate_nutrition(final_recipe, policy=policy)
    final_check = check_tolerance(final, targets.target)
    if recipe.mode == "database_menu" and (not final_check.passed or not _reviewed_quantities_hold(
            alignment.recipe, final_recipe, targets.total_n)):
        alternative, reason = _search_cookable(alignment.recipe, targets.total_n, targets, policy)
        if alternative is None:
            return MealValidationResult(alignment.check, final_recipe, final, final_check, False,
                                        tuple(blockers), reason)
        final_recipe = alternative
        final = calculate_nutrition(final_recipe, policy=policy)
        final_check = check_tolerance(final, targets.target)
    return MealValidationResult(alignment.check, final_recipe, final, final_check, final_check.passed,
                                tuple(blockers), None if final_check.passed else "final_nutrition_outside_tolerance_or_incomplete")
