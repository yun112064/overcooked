"""Trusted backend bridge from T01/T11 recipe IDs and T03/T04 to T12.

No HTTP, adoption, RAG, or generation orchestration. Profiles and NutritionPolicy
must come from the backend's reviewed data, never from browser/AI assertions.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy import MetaData, Table, select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from .service import (IngredientQuantity, MealValidationResult, Number,
                      NutritionPolicy, RecipeCandidate, calculate_nutrition, exact, validate_meal,
                      within_tolerance)
from .targets import MealTargets, PlanningInputError, targets_from_t03


class RecipeDataUnavailable(Exception):
    """DB/schema failure, distinct from incomplete or infeasible recipe data."""
    code = "RECIPE_DATA_UNAVAILABLE"


@dataclass(frozen=True)
class RecipeRows:
    recipe_ids: tuple[str, ...]
    recipes: tuple[Mapping, ...]
    ingredients: tuple[Mapping, ...]
    steps: tuple[Mapping, ...] = ()


@dataclass(frozen=True)
class InputIssue:
    code: str
    recipe_id: str
    line_id: str | None = None
    field: str | None = None
    input_digest: str | None = None

    @property
    def message(self) -> str:
        labels = {
            'nutrition_selection_missing': '缺少營養資料選擇',
            'nutrition_data_current_batch_missing': '目前沒有可用的營養資料批次',
            'nutrition_record_not_found_for_import_and_food': '食材營養資料缺失',
            'nutrition_food_id_missing': '食材營養資料缺失',
            'nutrition_variant_decision_required': '烹調方式有多種營養資料，等待 LLM 判斷',
            'llm_variant_decision_invalid': 'LLM 烹調方式判斷格式無效',
            'llm_variant_decision_mismatch': 'LLM 烹調方式判斷與 Food_ID 或營養資料不一致',
            'invalid_llm_variant_decision': 'LLM 回傳格式錯誤',
            'nutrition_variant_mismatch': '生熟或烹調方式不符合',
            'nutrition_state_unknown': '烹調生熟狀態無法判定',
            'tfda_source_missing_or_ambiguous': 'TFDA 營養來源無法唯一判定',
            'staple_nutrition_selection_missing': '主食份量／營養依據缺值',
            'staple_tfda_method_mismatch': '主食烹調方式與營養樣品不一致',
            'staple_tfda_sample_mismatch': '主食 TFDA 樣品不一致',
            'staple_policy_unavailable': '主食份量／營養依據缺值',
            'llm_raw_energy_missing': '缺少生食熱量基礎資料',
            'llm_raw_protein_missing': '缺少生食蛋白質基礎資料',
            'llm_not_configured': 'LLM 服務未設定',
            'llm_provider_error': 'LLM 服務錯誤',
            'invalid_llm_estimate': 'LLM 回傳格式錯誤，缺少有效產率或營養保留率',
            'invalid_llm_endpoint': 'LLM 服務網址設定錯誤',
            'invalid_llm_model': 'LLM 模型設定錯誤',
            'invalid_llm_provider': 'LLM 服務供應者設定錯誤',
            'cooked_weight_or_nutrients_unavailable': '缺少熟食產率或營養保留率',
            'llm_estimation_failed': 'LLM 熟食估算失敗',
            'cooking_method_unavailable': '烹調方式無法判定',
            't04_import_snapshot_mismatch': 'LLM 估算與營養資料版本不一致',
            't04_parameters_missing_invalid_or_sample_mismatch': 'LLM 烹調參數缺失或與食材樣品不一致',
            'approved_cooking_evidence_missing': '缺少已驗證的烹調依據',
            'llm_service_unavailable': 'LLM 服務無法使用',
            'llm_weight_missing': '缺少烹調前重量',
            'nutrition_recipe_derived_only': '此食材只有食譜網站營養（沒有熱量），需對照 TFDA 品項',
            'nutrition_cooked_only_needs_raw_mapping': '此食材只有熟食版本營養，需對照 TFDA 生食品項',
            'tfda_food_identity_unverified': 'TFDA 品項名稱無法對應，需確認是哪一種',
            'tfda_official_average_excluded': 'TFDA 官方平均值被排除，原因不是重複計算',
            'usda_source_ambiguous': 'USDA 有多筆資料，無法唯一判定',
            'reviewed_gram_conversion_missing': '缺少食材克數',
            'staple_not_supported': '目前只有白飯、糙米飯有已確認的份量與營養依據',
            'staple_selection_required': '尚未選擇主食',
            'nutrition_mapping_invalid': '營養對照設定檔格式錯誤，未套用',
            'nutrition_mapping_target_missing': '營養對照指定的 Food_ID 或 TFDA 品項在目前資料中找不到',
            'nutrition_mapping_mismatch': '營養對照與食材 Food_ID 不一致',
            'nutrition_supplement_invalid': '營養補充資料檔格式錯誤，未套用',
        }
        return labels.get(self.code, '營養或烹調依據缺失，無法採用')

    @property
    def adoption_blocked(self) -> bool:
        return True


@dataclass(frozen=True)
class RecipeLineProfile:
    """Reviewed line-specific units/state; digest prevents stale reuse."""
    food_id: str
    input_digest: str
    weight_basis: str
    consumed_as_is: bool
    cooking_step_g: Number | None
    source: str
    source_version: str
    review_status: str = "pending"
    cooking_method: str | None = None
    evidence_id: str | None = None
    cooking_context: Mapping[str, str | bool] | None = None
    # Explicit reviewed conversions, required if DB grams/range are unverified.
    reviewed_weight_g: Number | None = None
    reviewed_min_g: Number | None = None
    reviewed_max_g: Number | None = None


FACTOR_ONE_ASSUMPTION = 'raw_weight_energy_protein_factor_one'
# Selection reasons that an approved T12 supplement may resolve.
SUPPLEMENTABLE_CODES = frozenset({'nutrition_recipe_derived_only', 'nutrition_cooked_only_needs_raw_mapping',
                                  'nutrition_food_id_missing', 'nutrition_state_unknown'})


BROWN_RICE_VERSION = 'staples-brown-rice-2026-11-02-v1'
_BROWN_RICE_FIELDS = {'name': '糙米飯', 'food_id': 'FOOD001487', 'weight_basis': 'cooked',
                      'grams_per_n': '160', 'adjustable_min_g_per_n': '100',
                      'adjustable_max_g_per_n': '250', 'cooking_step_g': '1'}


def _decimal_text(value: Fraction) -> str:
    """Portion factors as plain decimals ("0.08"); a non-terminating one stays a fraction."""
    den = value.denominator
    for prime in (2, 5):
        while den % prime == 0:
            den //= prime
    if den != 1:
        return str(value)
    return format((Decimal(value.numerator) / Decimal(value.denominator)).normalize(), 'f')


ALL_NUTRIENTS = ('energy_kcal', 'protein_g', 'fat_g', 'carbohydrate_g', 'sugars_g', 'sodium_mg', 'dietary_fiber_g')
# Plain water carries no nutrients; its blank cells count as 0 for the display-only nutrients.
WATER_NAMES = frozenset({'水', '開水', '清水', '冷水', '熱水', '溫水', '冰水', '飲用水'})


def _shares_summing_to_one(shares: Sequence[Fraction]) -> list[Fraction]:
    """Round shares to 0.01 so they add up to exactly 1 (largest remainder first)."""
    hundredths = [share * 100 for share in shares]
    floors = [int(h) for h in hundredths]
    remaining = 100 - sum(floors)
    order = sorted(range(len(shares)), key=lambda i: (-(hundredths[i] - floors[i]), i))
    for i in order[:max(remaining, 0)]:
        floors[i] += 1
    return [Fraction(f, 100) for f in floors]


def _brown_rice_policy(conversion: str, nutrition_source: str) -> tuple[Mapping | None, InputIssue | None]:
    """Reviewed brown-rice staple (factor-one imported preview only)."""
    if conversion != 'factor_one' or nutrition_source != 'imported':
        return None, InputIssue('staple_not_supported', 'staple', field='staple')
    try:
        config = json.loads((Path(__file__).resolve().parents[1] / 'config/staples.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, InputIssue('staple_policy_unavailable', 'staple', field='policy')
    brown = config.get('brown_rice') if isinstance(config, dict) else None
    nutrition = brown.get('nutrition') if isinstance(brown, dict) else None
    values = nutrition.get('values') if isinstance(nutrition, dict) else None
    if (not isinstance(brown, dict) or brown.get('version') != BROWN_RICE_VERSION
            or brown.get('review_status') != 'user_confirmed'
            or any(brown.get(key) != value for key, value in _BROWN_RICE_FIELDS.items())
            or not brown.get('approved_by') or not brown.get('approved_on')
            or not isinstance(nutrition, dict) or nutrition.get('provider') not in ('TFDA', 'USDA', 'MEXT')
            or nutrition.get('state') != 'cooked'
            or not all(nutrition.get(key) for key in ('source_record_id', 'source_name', 'source_url',
                                                       'retrieved_on', 'basis'))
            or not isinstance(values, dict) or not {'energy_kcal', 'protein_g'} <= set(values)):
        return None, InputIssue('staple_policy_unreviewed_or_changed', 'staple', field='policy')
    try:
        if any(exact(value) < 0 for value in values.values()):
            raise ValueError
    except (ValueError, TypeError, PlanningInputError):
        return None, InputIssue('staple_policy_unreviewed_or_changed', 'staple', field='policy')
    return brown, None


def _supplement_record(line: Mapping, supplement) -> Mapping:
    """Line record built only from an approved T12 supplement entry."""
    return {
        'ingredient_id': supplement.record_id, 'state': supplement.state,
        'standard_name': supplement.name or line.get('canonical_name'),
        'nutrition_per_100g': {key: exact(value) for key, value in supplement.values.items()},
        'source': {'provider': supplement.provider, 'source_id': supplement.record_id,
                   'source_version': supplement.version, 'food_id': line['food_id'],
                   'source_url': supplement.source_url, 'retrieved_on': supplement.retrieved_on,
                   'value_basis': ('user_confirmed_per_100g' if supplement.provider == 'USER_CONFIRMED'
                                   else 'public_supplement_per_100g'),
                   'supplement': supplement.provenance},
    }


def _apply_fill(record: Mapping, fills: Mapping) -> Mapping:
    """Fill only blank cells of the record's own nutrition Food_ID."""
    source = record.get('source') or {}
    mapping = source.get('nutrition_mapping') or {}
    food = mapping.get('nutrition_food_id') or source.get('food_id')
    fill = fills.get(food)
    if fill is None:
        return record
    values = dict(record.get('nutrition_per_100g') or {})
    filled = {key: value for key, value in fill.values.items() if values.get(key) is None}
    if not filled:
        return record
    values.update({key: exact(value) for key, value in filled.items()})
    return {**record, 'nutrition_per_100g': values,
            'source': {**source, 'filled_values': {**fill.provenance, 'filled': sorted(filled)}}}


def recipe_level_profiles(rows: RecipeRows) -> Mapping[str, RecipeLineProfile]:
    """Build T12 input profiles from the reviewed recipe-level cooking facts.

    PR17 supplies a reviewed gram weight on every ingredient row.  Cooking is a
    property of the recipe: a unique recipe/step method is applied to its
    ingredient rows; several methods are left for the variant LLM together
    with the ordered steps.  No per-ingredient unit conversion, yield, range,
    or cooking-evidence record is required at this boundary.
    """
    recipes = {recipe['recipe_id']: recipe for recipe in rows.recipes}
    step_methods: dict[str, set[str]] = {}
    for step in rows.steps:
        method = step.get('cooking_method')
        if isinstance(method, str) and method.strip():
            step_methods.setdefault(step['recipe_id'], set()).add(method.strip())
    result: dict[str, RecipeLineProfile] = {}
    for line in rows.ingredients:
        recipe = recipes.get(line['recipe_id'], {})
        methods = set(step_methods.get(line['recipe_id'], set()))
        source_methods = recipe.get('cooking_methods')
        if isinstance(source_methods, (list, tuple)):
            methods.update(method.strip() for method in source_methods
                           if isinstance(method, str) and method.strip())
        method = next(iter(methods)) if len(methods) == 1 else None
        result[line['recipe_ingredient_id']] = RecipeLineProfile(
            food_id=line['food_id'], input_digest=recipe_line_digest(recipe, line),
            # The selected observation later determines raw versus cooked.
            # Recipe grams are stored to six decimal places.  In the absence
            # of a separately reviewed cooking unit, preserve that exact
            # recipe precision instead of inventing a coarser unit.
            weight_basis='cooked', consumed_as_is=True, cooking_step_g=Decimal('0.000001'),
            source='recipe-level-cooking', source_version='pr17-reviewed-weights-v1',
            review_status='approved', cooking_method=method,
            cooking_context={'method': method} if method else None)
    return result


@dataclass(frozen=True)
class PreparedRecipe:
    recipe: RecipeCandidate | None
    issues: tuple[InputIssue, ...]


@dataclass(frozen=True)
class DatabaseMenuResult:
    targets: MealTargets
    preparation: PreparedRecipe
    validation: MealValidationResult | None
    staple_snapshot: Mapping | None = None
    dish_portions: Mapping | None = None
    unscaled_baseline: Any = None
    # line_id → the seven nutrients of that final line (display only; None when the source has no value)
    line_nutrients: Mapping[str, Mapping[str, Fraction | None]] | None = None
    # line_id → blank display-only nutrients filled by category rules or cached estimates (trace only)
    display_fills: Mapping[str, list] | None = None
    # line_id → {recipe_id, name, food_id, unit, grams (recipe grams)}, for showing the amounts per dish.
    line_labels: Mapping[str, Mapping[str, str | None]] | None = None
    # recipe_id → ordered step texts, shown with the dish.
    recipe_steps: Mapping[str, tuple[str, ...]] | None = None

    @property
    def dishes(self) -> tuple[Mapping[str, object], ...]:
        """Whole-meal amounts of every validated line and the recipe steps, grouped by dish in menu order."""
        validation = self.validation
        labels = self.line_labels or {}
        steps = self.recipe_steps or {}
        grouped: dict[str, list[Mapping[str, str | None]]] = {}
        # Validated (portioned) amounts when the menu passed; otherwise the recipe's own amounts,
        # so ingredients and steps always show even when nutrition could not be calculated.
        adjusted = bool(validation and validation.final_recipe)
        if validation and validation.final_recipe:
            for line in validation.final_recipe.ingredients:
                label = labels.get(line.line_id) or {'recipe_id': '', 'name': line.food_id, 'food_id': line.food_id,
                                                     'unit': None}
                grouped.setdefault(str(label['recipe_id']), []).append(
                    {'line_id': line.line_id, 'name': label['name'], 'food_id': label['food_id'], 'unit': label['unit'],
                     'grams': str(exact(line.amount_g))})
            order = list(validation.final_recipe.recipe_ids)
        else:
            for line_id, label in labels.items():
                grouped.setdefault(str(label['recipe_id']), []).append(
                    {'line_id': line_id, 'name': label['name'], 'food_id': label['food_id'], 'unit': label['unit'], 'grams': label['grams']})
            order = list(dict.fromkeys(str(label['recipe_id']) for label in labels.values()))
        order = [rid for rid in order if rid in grouped and rid != 'staple']
        return tuple({'recipe_id': rid, 'ingredients': grouped[rid], 'steps': list(steps.get(rid, ())),
                      'adjusted': adjusted} for rid in order)

    def _line_name(self, line_id: str) -> str:
        if line_id.startswith('staple:') and self.staple_snapshot:
            return str(self.staple_snapshot.get('name') or '主食')
        return str(((self.line_labels or {}).get(line_id) or {}).get('name') or line_id)

    def _display_lines(self) -> dict[str, dict[str, Fraction | None]]:
        """Per-line seven nutrients for display; water lines count as 0 where the source is blank."""
        # Recipe names can carry a note after a space (e.g. 「水 各」); the first word decides.
        water = {lid for lid in (self.line_nutrients or {}) if (self._line_name(lid).split() or [''])[0] in WATER_NAMES}
        return {lid: {key: (Fraction(0) if value is None and lid in water else value)
                      for key, value in nutrients.items()}
                for lid, nutrients in (self.line_nutrients or {}).items()}

    def _seven_totals(self) -> dict[str, Fraction | None]:
        lines = list(self._display_lines().values())
        totals: dict[str, Fraction | None] = {}
        for key in ALL_NUTRIENTS:
            values = [n.get(key) for n in lines]
            known = [v for v in values if v is not None]
            totals[key] = sum(known, Fraction()) if lines and len(known) == len(values) else None
        return totals

    @property
    def all_nutrients(self) -> Mapping[str, object]:
        """The seven nutrients of the final menu, for display only (only energy and protein are validated).

        A nutrient is summed only when every line has a value; otherwise it is None, with the
        known partial sum and the ingredients lacking it, never a silent 0.
        """
        totals: dict[str, str | None] = {}
        known: dict[str, str] = {}
        missing: dict[str, list[str]] = {}
        lines = self._display_lines()
        for key in ALL_NUTRIENTS:
            values = [(line_id, nutrients.get(key)) for line_id, nutrients in lines.items()]
            have = [v for _, v in values if v is not None]
            lacking = sorted({self._line_name(lid) for lid, v in values if v is None})
            if not lines:
                totals[key] = None
            elif lacking:
                totals[key] = None
                known[key] = str(sum(have, Fraction()))
                missing[key] = lacking
            else:
                totals[key] = str(sum(have, Fraction()))
        filled = [{'ingredient': self._line_name(lid), **fill}
                  for lid, fills in (self.display_fills or {}).items() if lid in lines for fill in fills]
        return {'totals': totals, 'known_partial': known, 'missing_by_nutrient': missing, 'display_fills': filled}

    @property
    def per_person(self) -> tuple[Mapping[str, object], ...]:
        """Each participant's (or bento's) share of the validated menu, by their own target.

        A participant gets (own energy target / meal energy target) of every cooked
        dish and of the staple, so their energy follows their own T03 target.
        ``dish_share`` is that share of each whole dish, rounded to 0.01 so that all
        participants add up to exactly 1 (largest remainder); ``share`` keeps the
        exact value used for the nutrition.  Protein comes with the same food and may
        land elsewhere; ``within_10pct`` reports each nutrient against that person's
        own T12 tolerance (energy ≤ 110%, protein ≥ 90%; field name kept for the API).
        """
        validation = self.validation
        recipe = self.preparation.recipe
        if not validation or not validation.nutrition_passed or not validation.final_estimate or recipe is None:
            return ()
        staple_g = (exact(self.staple_snapshot['final_grams'])
                    if self.staple_snapshot and self.staple_snapshot.get('final_grams') else None)
        totals = validation.final_estimate.totals
        meal_energy = self.targets.target['energy_kcal']
        meal_all = self._seven_totals()
        meal_known = dict(cast(Mapping[str, str], self.all_nutrients['known_partial']))
        items = list(self.targets.snapshot.get('participations') or [])
        exact_shares = [exact(item['scaled_target']['energy_kcal']) / meal_energy for item in items]
        shown_shares = _shares_summing_to_one(exact_shares)
        people = []
        for item, share, shown in zip(items, exact_shares, shown_shares):
            own = {key: exact(item['scaled_target'][key]) for key in ('energy_kcal', 'protein_g')}
            eaten = {key: None if (value := totals.get(key)) is None else value * share for key in own}
            eaten_all = {key: None if value is None else value * share for key, value in meal_all.items()}
            known_all = {key: Fraction(value) * share for key, value in meal_known.items()}
            people.append({
                'participation_id': item['participation_id'], 'member_id': item['member_id'],
                'kind': item['kind'], 'meal': item['meal'], 'n': item['n'], 'share': str(share),
                # 這一份應占每日的比例（餐別比例 × n），與每日目標（當餐目標 ÷ 該比例）
                'meal_ratio': str(exact(item['meal_ratio']) * exact(item['n'])),
                'daily': {key: str(own[key] / (exact(item['meal_ratio']) * exact(item['n']))) for key in own},
                'dish_share': _decimal_text(shown),
                'dish_ids': [rid for rid in recipe.recipe_ids if rid != 'staple'],
                # Same rounded share as the dishes, so everyone's staple adds up to the cooked amount.
                'staple_g': str(staple_g * shown) if staple_g is not None else None,
                'target': {key: str(value) for key, value in own.items()},
                'nutrition': {key: None if value is None else str(value) for key, value in eaten.items()},
                'nutrition_all': {key: None if value is None else str(value) for key, value in eaten_all.items()},
                'nutrition_known_partial': {key: str(value) for key, value in known_all.items()},
                'within_10pct': {key: value is not None and within_tolerance(value, own[key], key)
                                 for key, value in eaten.items()},
            })
        return tuple(people)

    @property
    def input_issues(self) -> tuple[Mapping[str, object], ...]:
        issues = self.preparation.issues
        return tuple({'code': issue.code, 'recipe_id': issue.recipe_id,
                      'line_id': issue.line_id, 'field': issue.field,
                      'message': issue.message, 'adoption_blocked': issue.adoption_blocked}
                     for issue in issues)

    @property
    def adoption_blocked(self) -> bool:
        return bool(self.preparation.issues or self.validation is None
                    or not self.validation.nutrition_passed
                    or self.validation.adoption_blockers)

    @property
    def final_food_ids(self) -> tuple[str, ...]:
        """Only validated final ingredients, for the caller's later RAG step."""
        if not self.validation or not self.validation.nutrition_passed or not self.validation.final_recipe:
            return ()
        return tuple(sorted({line.food_id for line in self.validation.final_recipe.ingredients
                             if exact(line.amount_g) > 0}))

    def to_dict(self) -> Mapping[str, object]:
        """Stable backend payload; missing values are issues, never zeroes."""
        validation = self.validation
        estimate = validation.final_estimate if validation else None
        check = validation.final_check if validation else None

        def wire(value: Any) -> Any:
            """Make exact Fraction/Decimal receipts JSON-safe without rounding."""
            if isinstance(value, (Decimal,)) or hasattr(value, 'numerator') and hasattr(value, 'denominator'):
                return str(value)
            if isinstance(value, Mapping):
                return {str(key): wire(item) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [wire(item) for item in value]
            return value

        source_estimate = estimate or (validation.baseline_estimate if validation else None)
        sources = wire(source_estimate.sources) if source_estimate else []
        def is_estimated_source(source: Mapping[str, object]) -> bool:
            provenance = source.get('source')
            evidence = source.get('cooking_evidence')
            return (isinstance(provenance, Mapping) and bool(provenance.get('variant_decision'))
                    or isinstance(evidence, Mapping) and evidence.get('is_estimated') is True)
        estimated = any(isinstance(source, Mapping) and is_estimated_source(source)
                        for source in source_estimate.sources) if source_estimate else False
        baseline = self.unscaled_baseline or (validation.baseline_estimate if validation else None)
        baseline_complete = bool(baseline and not baseline.missing
                                 and all(value is not None for value in baseline.totals.values()))
        # Three different outcomes must not look alike in the preview:
        # data missing (cannot calculate), calculated but outside the tolerance
        # (energy ≤ 110%, protein ≥ 90%), and calculated and passed.  Adoption-policy approval is
        # reported separately in `adoption_blockers`.
        if self.preparation.issues or validation is None or not baseline_complete:
            status = 'calculation_incomplete'
        elif validation.nutrition_passed:
            status = 'nutrition_passed'
        else:
            status = 'outside_tolerance'
        return {
            'status': status,
            'status_message': {'calculation_incomplete': '營養資料不足，無法完成計算',
                               'outside_tolerance': '營養已計算，但熱量會超過目標 10% 或蛋白質不足目標 90%',
                               'nutrition_passed': ('營養計算通過（已調整菜餚份量）' if self.dish_portions
                                                    else '營養計算通過')}[status],
            'dish_portions': dict(self.dish_portions) if self.dish_portions else None,
            'per_person': self.per_person,
            'nutrition_all': self.all_nutrients,
            'dishes': self.dishes,
            'targets': {key: str(value) for key, value in self.targets.target.items()},
            'input_issues': self.input_issues,
            'adoption_blocked': self.adoption_blocked,
            'final_food_ids': self.final_food_ids,
            'staple': dict(self.staple_snapshot) if self.staple_snapshot else None,
            'nutrition_passed': bool(validation and validation.nutrition_passed),
            'adoption_blockers': tuple(validation.adoption_blockers) if validation else (),
            'nutrition': {
                'totals': wire(estimate.totals) if estimate else None,
                'baseline_per_serving': wire(baseline.totals) if baseline else None,
                'missing': list(estimate.missing) if estimate else [],
                'difference': wire(check.difference) if check else None,
                'bounds': wire(check.bounds) if check else None,
                'sources': sources,
                'is_llm_estimated': estimated,
                'conversion_assumptions': sorted({str(item['source'].get('conversion_assumption'))
                                                  for item in sources if isinstance(item, Mapping)
                                                  and isinstance(item.get('source'), Mapping)
                                                  and item['source'].get('conversion_assumption')}),
                'nutrition_mappings': sorted({str(item['source']['nutrition_mapping']['mapping_version'])
                                              for item in sources if isinstance(item, Mapping)
                                              and isinstance(item.get('source'), Mapping)
                                              and isinstance(item['source'].get('nutrition_mapping'), Mapping)}),
                'supplements': sorted({str(item['source'].get('supplement', {}).get('supplement_version')
                                           or item['source'].get('filled_values', {}).get('supplement_version'))
                                       for item in sources if isinstance(item, Mapping)
                                       and isinstance(item.get('source'), Mapping)
                                       and (item['source'].get('supplement') or item['source'].get('filled_values'))}),
                'providers': sorted({str(item['source'].get('provider')) for item in sources
                                     if isinstance(item, Mapping) and isinstance(item.get('source'), Mapping)}),
            },
        }


def estimate_missing_cooking_with_t04(rows: RecipeRows, *, profiles: Mapping[str, RecipeLineProfile],
                                      line_records: Mapping[str, Mapping], estimator) -> tuple[Mapping[str, object], tuple[InputIssue, ...]]:
    """Run the existing T04 estimator only for selected raw records without cooked data.

    The estimator receives TFDA raw values and returns parameters only. T12 still
    performs all nutrition arithmetic and rejects incomplete receipts.
    """
    results: dict[str, object] = {}
    issues: list[InputIssue] = []
    for line in rows.ingredients:
        lid = line['recipe_ingredient_id']
        record = line_records.get(lid)
        profile = profiles.get(lid)
        if not isinstance(record, Mapping) or not isinstance(profile, RecipeLineProfile):
            continue
        if record.get('state') != 'raw':
            continue
        if not profile.cooking_method:
            issues.append(InputIssue('cooking_method_unavailable', line['recipe_id'], lid, 'cooking_method'))
            continue
        # Only raw/undetermined observations need T04.  A menu whose selected
        # observations are already cooked must not fail merely because the
        # optional T04 module is unavailable.
        try:
            from cooked_nutrition_service import calculate_cooking  # type: ignore[import-not-found]
        except ImportError:
            issues.append(InputIssue('llm_service_unavailable', line['recipe_id'], lid, 'estimator'))
            continue
        if estimator is None:
            try:
                from llm_cooking_estimator import estimate_cooking  # type: ignore[import-not-found]
                estimator = estimate_cooking
            except ImportError:
                issues.append(InputIssue('llm_service_unavailable', line['recipe_id'], lid, 'estimator'))
                continue
        raw_source = record.get('source')
        raw_values = {key: float(value) if value is not None else None
                      for key, value in dict(record.get('nutrition_per_100g') or {}).items()}
        raw = {'ingredient_id': record.get('ingredient_id'),
               'standard_name': record.get('standard_name'), 'state': 'raw',
               'nutrition_per_100g': raw_values,
               'source': dict(raw_source) if isinstance(raw_source, Mapping) else {}}
        if raw['nutrition_per_100g'].get('energy_kcal') is None:
            issues.append(InputIssue('llm_raw_energy_missing', line['recipe_id'], lid, 'energy_kcal'))
            continue
        if raw['nutrition_per_100g'].get('protein_g') is None:
            issues.append(InputIssue('llm_raw_protein_missing', line['recipe_id'], lid, 'protein_g'))
            continue
        try:
            weight = line.get('weight_g')
            if not isinstance(weight, (int, float, Decimal)) or isinstance(weight, bool):
                issues.append(InputIssue('llm_weight_missing', line['recipe_id'], lid, 'weight_g'))
                continue
            result = calculate_cooking(raw['standard_name'] or profile.food_id, float(weight), profile.cooking_method,
                                       [raw], lambda _: raw, cooking_context=profile.cooking_context or {},
                                       estimator=estimator)
        except (TypeError, ValueError, KeyError, OSError, TimeoutError):
            issues.append(InputIssue('llm_estimation_failed', line['recipe_id'], lid, 'cooking'))
            continue
        if not result.get('cooking_adjustment_complete'):
            issues.append(InputIssue(result.get('missing_reason', 'llm_estimation_failed'),
                                     line['recipe_id'], lid, 'cooking'))
            continue
        results[lid] = result
    return results, tuple(issues)


def load_database_recipe_rows(db: Connection, recipe_ids: Sequence[str]) -> RecipeRows:
    if (isinstance(recipe_ids, (str, bytes)) or not recipe_ids
            or any(not isinstance(i, str) or not i.strip() for i in recipe_ids)
            or len(set(recipe_ids)) != len(recipe_ids)):
        raise PlanningInputError("Provide distinct recipe IDs selected by T11.")
    ids = tuple(recipe_ids)
    try:
        metadata = MetaData()
        recipes = Table("recipes", metadata, autoload_with=db)
        ingredients = Table("recipe_ingredients", metadata, autoload_with=db)
        # Read every line: available ingredients, oil and seasonings included.
        recipe_rows = tuple(dict(r) for r in db.execute(
            select(recipes).where(recipes.c.recipe_id.in_(ids))).mappings())
        ingredient_rows = tuple(dict(r) for r in db.execute(
            select(ingredients).where(ingredients.c.recipe_id.in_(ids))
            .order_by(ingredients.c.recipe_id, ingredients.c.sequence)).mappings())
        try:
            steps = Table("recipe_steps", metadata, autoload_with=db)
            step_rows = tuple(dict(r) for r in db.execute(
                select(steps).where(steps.c.recipe_id.in_(ids)).order_by(steps.c.recipe_id, steps.c.step_no)
            ).mappings())
        except (SQLAlchemyError, AttributeError):
            step_rows = ()
    except (SQLAlchemyError, AttributeError):
        raise RecipeDataUnavailable() from None
    return RecipeRows(ids, recipe_rows, ingredient_rows, step_rows)


def recipe_line_digest(recipe: Mapping, line: Mapping) -> str:
    """Bind the reviewed profile to all stored recipe and ingredient fields."""
    payload = json.dumps({"recipe": dict(recipe), "ingredient": dict(line)},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def prepare_database_recipe(rows: RecipeRows, *, policy: NutritionPolicy,
                            profiles: Mapping[str, RecipeLineProfile]) -> PreparedRecipe:
    issues: list[InputIssue] = []
    prepared: list[IngredientQuantity] = []
    recipes = {r["recipe_id"]: r for r in rows.recipes}
    seen_lines: set[str] = set()
    for rid in rows.recipe_ids:
        recipe = recipes.get(rid)
        if recipe is None:
            issues.append(InputIssue("recipe_not_found", rid))
            continue
        # Recipe qualification, rights and provenance belong to the upstream
        # import/T11 owner.  T12 accepts the supplied recipe rows and checks
        # only nutrition-calculation facts below.
        lines = [line for line in rows.ingredients if line["recipe_id"] == rid]
        if not lines:
            issues.append(InputIssue("recipe_has_no_ingredients", rid))
        for row in lines:
            lid = row["recipe_ingredient_id"]
            digest = recipe_line_digest(recipe, row)
            def issue(code: str, field: str | None = None) -> None:
                issues.append(InputIssue(code, rid, lid, field, digest))
            if not isinstance(lid, str) or not lid or lid in seen_lines:
                raise PlanningInputError("Recipe ingredient IDs must be unique.")
            seen_lines.add(lid)
            start = len(issues)
            def number(value, field: str, *, positive: bool = False):
                try:
                    result = exact(value)
                    if result < 0 or (positive and result == 0):
                        raise PlanningInputError("Invalid amount")
                    return result
                except PlanningInputError:
                    issue("missing_or_invalid_value", field)
                    return None
            servings = number(recipe.get("original_servings"), "original_servings", positive=True)
            required = row.get("is_required")
            if required is None:
                # The PR17 workbook has no is_required column; every listed
                # ingredient is used with its recipe grams (never dropped to 0).
                required = True
            if type(required) is not bool:
                issue("required_flag_unknown", "is_required")
            sample = policy.sample_for(lid, row["food_id"])
            if not isinstance(sample, str) or not sample:
                issue("tfda_mapping_missing", "sample_id")
            profile = profiles.get(lid)
            if profile is None:
                issue("reviewed_line_profile_missing", "profile")
                continue
            if profile.food_id != row["food_id"] or profile.input_digest != digest:
                issue("line_profile_unreviewed_or_stale", "profile")
                continue
            if profile.weight_basis not in ("purchase", "raw", "cooked"):
                issue("weight_basis_unknown", "weight_basis")
            if type(profile.consumed_as_is) is not bool:
                issue("consumed_as_is_unknown", "consumed_as_is")
            if policy.line_nutrition_records is not None:
                record = policy.line_nutrition_records.get(lid, {})
                if (record.get('state') == 'cooked' and profile.cooking_method is not None
                        and record.get('source', {}).get('cooking_method') != profile.cooking_method):
                    issue('imported_cooking_method_mismatch', 'cooking_method')
            step = number(profile.cooking_step_g, "cooking_step_g", positive=True)
            # PR17's recipe workbook has manually reviewed `weight_g` for
            # every row.  Unit text remains provenance/display only: T12 must
            # use this stored gram value and never re-convert or ask an LLM to
            # infer a cup/spoon/piece weight.
            # User decision 2026-11-01/02: the workbook `weight_g` column (kept
            # by the importer as `source_weight_g`) is the recipe author's
            # gram value, and their back-calculated nutrition uses it.  The
            # importer's `weight_g` copies the `amount` column when the unit
            # is g, which disagrees with the workbook in 205 rows (e.g. 冬瓜
            # 2大片: amount 50 g, weight_g 750).  Use the workbook column.
            if row.get("source_weight_g") is not None:
                weight_value = row.get("source_weight_g")
            elif row.get("weight_g") is not None:
                weight_value = row.get("weight_g")
            elif profile.reviewed_weight_g is not None:
                weight_value = profile.reviewed_weight_g
            else:
                weight_value = None
                issue("reviewed_gram_conversion_missing", "weight_g")
            weight = number(weight_value, "weight_g", positive=required is not False)
            minimum = maximum = None
            adjustment_status = 'not_reviewed'
            if row.get("adjustment_status") == "approved":
                if row.get("unit") == "g":
                    low, high = row.get("adjustable_min"), row.get("adjustable_max")
                else:
                    low, high = profile.reviewed_min_g, profile.reviewed_max_g
                # R06 works with fixed recipe grams when no reviewed range is
                # stored.  Existing explicit ranges remain an optional
                # constraint; their absence is not a data error.
                if low is not None and high is not None:
                    minimum = number(low, "adjustable_min_g")
                    maximum = number(high, "adjustable_max_g")
                    if (minimum is not None and maximum is not None and weight is not None
                            and not minimum <= weight <= maximum):
                        issue("reviewed_range_invalid", "adjustable_range")
                    else:
                        adjustment_status = 'approved'
            # Direct cooked observations need no per-line cooking evidence.
            # Raw/undetermined observations receive their conversion receipt
            # from T04/LLM; without it calculate_nutrition will report the
            # actual nutrition/conversion missing reason.
            evidence = policy.approved_evidence.get(profile.evidence_id) if profile.evidence_id else None
            if len(issues) != start:
                continue
            assert isinstance(sample, str) and weight is not None and isinstance(required, bool)
            assert servings is not None and step is not None
            prepared.append(IngredientQuantity(
                line_id=lid, food_id=row["food_id"], sample_id=sample, amount_g=weight,
                weight_basis=cast(Literal['purchase', 'raw', 'cooked'], profile.weight_basis),
                consumed_as_is=profile.consumed_as_is,
                cooking_step_g=step, cooking_method=profile.cooking_method, evidence=evidence,
                is_required=required, adjustment_status=adjustment_status,
                adjustable_min_g=minimum, adjustable_max_g=maximum, original_servings=servings,
                cooking_context=dict(profile.cooking_context or {})))
    if issues:
        return PreparedRecipe(None, tuple(issues))
    return PreparedRecipe(RecipeCandidate("database_menu", tuple(prepared), rows.recipe_ids, "approved"), ())


def _recipe_grams(row: Mapping) -> str | None:
    """The reviewed recipe grams T12 calculates with (``source_weight_g``, else ``weight_g``)."""
    grams = row.get('source_weight_g') if row.get('source_weight_g') is not None else row.get('weight_g')
    return None if grams is None else str(grams)


def validate_database_menu(db: Connection, recipe_ids: Sequence[str], *, rows: RecipeRows | None = None,
                           **kwargs) -> DatabaseMenuResult:
    """T12 for database recipes; every result also carries each dish's ingredients and steps.

    The ingredient names, recipe grams and steps come from the recipe rows, so they are
    shown even when nutrition could not be calculated (see ``_validate_database_menu``).
    """
    if rows is None:
        rows = load_database_recipe_rows(db, recipe_ids)
    result = _validate_database_menu(db, recipe_ids, rows=rows, **kwargs)
    line_labels = {row['recipe_ingredient_id']: {'recipe_id': row['recipe_id'], 'food_id': row['food_id'],
                                                 'name': row.get('canonical_name') or row['food_id'],
                                                 'unit': row.get('unit'), 'grams': _recipe_grams(row)}
                   for row in rows.ingredients}
    recipe_steps: dict[str, tuple[str, ...]] = {}
    for step in sorted(rows.steps, key=lambda s: (s['recipe_id'], s.get('step_no') or 0)):
        if step.get('instruction_clean'):
            recipe_steps[step['recipe_id']] = recipe_steps.get(step['recipe_id'], ()) + (step['instruction_clean'],)
    return replace(result, line_labels=line_labels, recipe_steps=recipe_steps)


def _validate_database_menu(db: Connection, recipe_ids: Sequence[str], *, rows: RecipeRows, participations: list,
                           target_service, policy: NutritionPolicy,
                           profiles: Mapping[str, RecipeLineProfile] | None = None,
                           staple: str | None = None,
                           t04_results: Mapping | None = None,
                           food_mapping_path=None,
                           nutrition_source: Literal['imported', 'legacy_tfda'] = 'imported',
                           nutrition_import_id: str | None = None,
                           nutrition_selections: Mapping | None = None,
                           cooking_estimator=None, variant_resolver=None,
                           conversion: Literal['t04', 'factor_one'] = 't04',
                           nutrition_mappings: Mapping | None = None,
                           nutrition_supplements=None, nutrition_resolver=None) -> DatabaseMenuResult:
    """Backend participations → exact T03 targets → all DB lines → T12.

    Caller retains household authorization, T11 selection/fallback and T14 flow.
    No public preview or caller-supplied adoption flag is used as a target.
    """
    targets = targets_from_t03(target_service.aggregate_meal_targets(participations))
    batch_issues = tuple(
        InputIssue("batch_servings_unresolved", recipe["recipe_id"], field="original_servings")
        for recipe in rows.recipes if recipe.get("servings_basis") == "batch"
    )
    if batch_issues:
        return DatabaseMenuResult(targets, PreparedRecipe(None, batch_issues), None)
    using_recipe_level_profiles = profiles is None
    profiles = dict(profiles) if profiles is not None else dict(recipe_level_profiles(rows))
    if staple is None:
        return DatabaseMenuResult(targets, PreparedRecipe(None, (
            InputIssue('staple_selection_required', 'staple', field='staple'),)), None)
    staple_snapshot = None
    staple_line_id = None
    staple_record = None
    if staple == '糙米飯':
        # User decision 2026-11-02: TFDA has no cooked brown rice, so the
        # reviewed USDA medium-grain cooked value is used for the staple only.
        brown, issue = _brown_rice_policy(conversion, nutrition_source)
        if issue is not None or brown is None:
            return DatabaseMenuResult(targets, PreparedRecipe(None, (issue,) if issue else ()), None)
        staple_line_id = f"staple:{brown['food_id']}"
        staple_snapshot = {key: value for key, value in brown.items() if key != 'nutrition'}
        staple_snapshot.update({'selection': staple, 'nutrition_provider': brown['nutrition']['provider'],
                                'nutrition_source_id': brown['nutrition']['source_record_id']})
        recipe_row = {'recipe_id': 'staple', 'original_servings': 1,
                      'review_status': 'approved', 'is_eligible': True}
        line_row = {'recipe_ingredient_id': staple_line_id, 'recipe_id': 'staple', 'sequence': 1,
                    'food_id': brown['food_id'], 'canonical_name': brown['name'],
                    'weight_g': brown['grams_per_n'], 'weight_status': 'source_explicit',
                    'unit': 'g', 'is_required': True, 'adjustment_status': 'approved',
                    'adjustable_min': brown['adjustable_min_g_per_n'],
                    'adjustable_max': brown['adjustable_max_g_per_n']}
        rows = RecipeRows(rows.recipe_ids + ('staple',), rows.recipes + (recipe_row,),
                          rows.ingredients + (line_row,), rows.steps)
        profiles = {**profiles, staple_line_id: RecipeLineProfile(
            brown['food_id'], recipe_line_digest(recipe_row, line_row), 'cooked', True,
            brown['cooking_step_g'], 'user-confirmed-staple', brown['version'],
            review_status='approved')}
        from .nutrition_supplement import NutritionSupplement
        n = brown['nutrition']
        staple_record = _supplement_record(line_row, NutritionSupplement(
            brown['food_id'], brown['name'], 'values', n['state'], dict(n['values']), n['provider'],
            n['source_name'], n['source_record_id'], n['source_url'], n['retrieved_on'], n['basis'],
            brown['approved_by'], brown['approved_on'], brown['version']))
    elif staple == '白飯':
        try:
            config = json.loads((Path(__file__).resolve().parents[1] / 'config/staples.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return DatabaseMenuResult(targets, PreparedRecipe(None, (
                InputIssue('staple_policy_unavailable', 'staple', field='policy'),)), None)
        expected_rice = {'name': '白飯', 'food_id': 'FOOD000296', 'tfda_sample_id': 'A0550601',
                         'weight_basis': 'cooked', 'source_cooking_method': '電鍋煮',
                         'grams_per_n': '160',
                         'adjustable_min_g_per_n': '100', 'adjustable_max_g_per_n': '250',
                         'cooking_step_g': '1'}
        if (not isinstance(config, dict) or config.get('version') != 'staples-2026-11-01-v2'
                or config.get('review_status') != 'user_confirmed'
                or config.get('white_rice') != expected_rice):
            return DatabaseMenuResult(targets, PreparedRecipe(None, (
                InputIssue('staple_policy_unreviewed_or_changed', 'staple', field='policy'),)), None)
        rice = expected_rice
        staple_snapshot = {'version': config['version'], 'review_status': config['review_status'],
                           **rice, 'selection': staple}
        lid = staple_line_id = 'staple:FOOD000296'
        recipe_row = {'recipe_id': 'staple', 'original_servings': 1,
                      'review_status': 'approved', 'is_eligible': True}
        line_row = {'recipe_ingredient_id': lid, 'recipe_id': 'staple', 'sequence': 1,
                    'food_id': rice['food_id'], 'canonical_name': rice['name'],
                    'weight_g': rice['grams_per_n'], 'weight_status': 'source_explicit',
                    'unit': 'g', 'is_required': True, 'adjustment_status': 'approved',
                    'adjustable_min': rice['adjustable_min_g_per_n'],
                    'adjustable_max': rice['adjustable_max_g_per_n']}
        rows = RecipeRows(rows.recipe_ids + ('staple',), rows.recipes + (recipe_row,),
                          rows.ingredients + (line_row,), rows.steps)
        profiles = {**profiles, lid: RecipeLineProfile(
            rice['food_id'], recipe_line_digest(recipe_row, line_row), 'cooked', True,
            rice['cooking_step_g'], 'user-confirmed-staple', config['version'],
            review_status='approved')}
        if nutrition_source == 'legacy_tfda':
            # The legacy catalog has no Food_ID selection path.  In particular,
            # never inject A0550601 as a required white-rice sample.
            return DatabaseMenuResult(targets, PreparedRecipe(None, (
                InputIssue('staple_nutrition_selection_missing', 'staple', lid, 'food_id'),)), None)
    elif staple not in ('不需要主食', '無主食', '沒有主食'):
        return DatabaseMenuResult(targets, PreparedRecipe(None, (
            InputIssue('staple_not_supported', 'staple', field='staple'),)), None)
    if nutrition_source == 'imported':
        if food_mapping_path is not None:
            raise PlanningInputError('Imported nutrition cannot use a legacy FOOD/TFDA registry.')
        from .imported_nutrition import (current_nutrition_import_id,
                                         load_imported_nutrition, resolve_imported_selections)
        if nutrition_import_id is None:
            nutrition_import_id = current_nutrition_import_id(db)
        if nutrition_import_id is None:
            return DatabaseMenuResult(targets, PreparedRecipe(None, (
                InputIssue('nutrition_data_current_batch_missing', '', field='import_id'),)), None)
        if nutrition_resolver is not None:
            if conversion != 'factor_one':
                raise PlanningInputError('Reconciled nutrition requires its pinned factor_one policy.')
            imported = nutrition_resolver(db, rows)
            if imported.issues:
                return DatabaseMenuResult(targets, PreparedRecipe(None, imported.issues), None)
        else:
            if nutrition_mappings is None and conversion == 'factor_one':
                from .nutrition_mapping import NutritionMappingInvalid, load_nutrition_mappings
                try:
                    nutrition_mappings = load_nutrition_mappings()
                except NutritionMappingInvalid:
                    return DatabaseMenuResult(targets, PreparedRecipe(None, (
                        InputIssue('nutrition_mapping_invalid', '', field='nutrition_mapping'),)), None)
            resolve_rows = rows if staple_record is None else RecipeRows(
                rows.recipe_ids, rows.recipes,
                tuple(row for row in rows.ingredients if row['recipe_ingredient_id'] != staple_line_id), rows.steps)
            resolved_selections, selection_issues = resolve_imported_selections(
                db, resolve_rows, import_id=nutrition_import_id, selections=nutrition_selections,
                profiles=profiles, variant_resolver=variant_resolver, conversion=conversion,
                mappings=nutrition_mappings)
            supplement_lines: dict[str, Mapping] = {}
            if staple_record is not None and staple_line_id is not None:
                supplement_lines[staple_line_id] = staple_record
            if conversion == 'factor_one':
                if nutrition_supplements is None:
                    from .nutrition_supplement import NutritionSupplementInvalid, load_nutrition_supplements
                    try:
                        nutrition_supplements = load_nutrition_supplements()
                    except NutritionSupplementInvalid:
                        return DatabaseMenuResult(targets, PreparedRecipe(None, (
                            InputIssue('nutrition_supplement_invalid', '', field='nutrition_supplement'),)), None)
                # A line whose Food_ID has no usable record may take approved
                # supplement values; everything else keeps its reported reason.
                remaining = []
                for issue in selection_issues:
                    line = next((row for row in rows.ingredients
                                 if row['recipe_ingredient_id'] == issue.line_id), None)
                    supplement = (nutrition_supplements.values.get(line['food_id'])
                                  if line is not None and issue.code in SUPPLEMENTABLE_CODES else None)
                    if supplement is None or line is None or issue.line_id is None:
                        remaining.append(issue)
                    else:
                        supplement_lines[issue.line_id] = _supplement_record(line, supplement)
                selection_issues = tuple(remaining)
            if selection_issues:
                return DatabaseMenuResult(targets, PreparedRecipe(None, selection_issues), None)
            loaded_rows = rows if not supplement_lines else RecipeRows(
                rows.recipe_ids, rows.recipes,
                tuple(row for row in rows.ingredients if row['recipe_ingredient_id'] not in supplement_lines),
                rows.steps)
            imported = load_imported_nutrition(db, loaded_rows, import_id=nutrition_import_id,
                                                selections=resolved_selections,
                                                allow_usda=conversion == 'factor_one')
            if imported.issues:
                return DatabaseMenuResult(targets, PreparedRecipe(None, imported.issues), None)
            if conversion == 'factor_one' and nutrition_supplements is not None:
                records = {lid: _apply_fill(record, nutrition_supplements.fills)
                           for lid, record in imported.line_records.items()}
                records.update(supplement_lines)
                imported = replace(imported, line_records=records)
        policy = replace(policy, sample_mappings={}, line_nutrition_records=imported.line_records)
        # Selection uses the recipe-level method.  A matching cooked sample is
        # already food after the recipe process, so its reviewed recipe grams
        # are consumed directly; raw/undetermined data keeps the recipe method
        # and enters the T04/LLM yield-retention path below.
        if using_recipe_level_profiles and conversion == 'factor_one':
            # User decision 2026-11-01: recipe grams are pre-cooking weights.
            # Energy and protein are not lost by water evaporation, so raw
            # composition x recipe grams is used with factor 1 (no yield or
            # retention estimate, no LLM).  The assumption is recorded on
            # every affected source.  Cooked/as-eaten records keep their basis.
            records = {}
            for lid, record in imported.line_records.items():
                record = dict(record)
                if record.get('state') == 'raw':
                    record['source'] = {**record['source'],
                                        'conversion_assumption': FACTOR_ONE_ASSUMPTION}
                records[lid] = record
            imported = replace(imported, line_records=records)
            policy = replace(policy, line_nutrition_records=imported.line_records)
            profiles = {lid: replace(profile,
                         weight_basis=('raw' if record.get('state') == 'raw' else 'cooked'),
                         consumed_as_is=True, cooking_method=None, cooking_context=None)
                        for lid, profile in profiles.items()
                        for record in (imported.line_records.get(lid, {}),)}
        elif using_recipe_level_profiles:
            profiles = {lid: replace(profile,
                         weight_basis=('raw' if record.get('state') == 'raw' else 'cooked'),
                         consumed_as_is=record.get('state') != 'raw',
                         cooking_method=(profile.cooking_method if record.get('state') == 'raw' else None),
                         cooking_context=(profile.cooking_context if record.get('state') == 'raw' else None))
                        for lid, profile in profiles.items()
                        for record in (imported.line_records.get(lid, {}),)}
        estimates, estimate_issues = ({}, ())
        if t04_results is None and conversion != 'factor_one':
            estimates, estimate_issues = estimate_missing_cooking_with_t04(
                rows, profiles=profiles, line_records=imported.line_records, estimator=cooking_estimator)
        if estimate_issues:
            return DatabaseMenuResult(targets, PreparedRecipe(None, estimate_issues), None)
        if estimates:
            from .t04_bridge import T04CookingResult, apply_t04_results
            receipts = {}
            for lid, estimate in estimates.items():
                line = next(row for row in rows.ingredients if row['recipe_ingredient_id'] == lid)
                profile = profiles[lid]
                source = imported.line_records[lid]['source']
                receipts[lid] = T04CookingResult(
                    profile.input_digest, profile.food_id, profile.weight_basis,
                    str(line.get('weight_g')), profile.cooking_method or '',
                    dict(profile.cooking_context or {}), 'T04-auto-estimator', cast(Any, estimate),
                    source.get('import_id'), source.get('nutrition_id'), source.get('nutrition_source_id'))
            t04_policy, profiles, t04_issues = apply_t04_results(
                rows, policy=policy, profiles=profiles, results=receipts)
            if t04_issues:
                return DatabaseMenuResult(targets, PreparedRecipe(None, t04_issues), None)
            policy = t04_policy
    elif nutrition_source == 'legacy_tfda':
        if nutrition_import_id is not None or nutrition_selections is not None:
            raise PlanningInputError('Legacy mode cannot consume imported nutrition selections.')
        policy = replace(policy, line_nutrition_records=None)
    else:
        raise PlanningInputError('Unknown nutrition source.')
    if food_mapping_path is not None:
        from nutrition_model.food_id_mapping import load_food_id_mappings
        names = {}
        for row in rows.ingredients:
            names.setdefault(row['food_id'], []).append(row.get('canonical_name'))
        try:
            mappings = load_food_id_mappings(food_mapping_path, policy.catalog_path, expected_names=names)
        except (OSError, ValueError, KeyError, TypeError):
            raise PlanningInputError('FOOD/TFDA mapping unavailable or incompatible.') from None
        if any(fid in policy.sample_mappings and policy.sample_mappings[fid] != sid
               for fid, sid in mappings.items()):
            raise PlanningInputError('Explicit FOOD/TFDA policy conflicts with the mapping registry.')
        policy = replace(policy, sample_mappings={**policy.sample_mappings, **mappings})
    if t04_results:
        from .t04_bridge import apply_t04_results
        policy, profiles, issues = apply_t04_results(rows, policy=policy, profiles=profiles,
                                                    results=t04_results)
        if issues:
            return DatabaseMenuResult(targets, PreparedRecipe(None, issues), None)
    preparation = prepare_database_recipe(rows, policy=policy, profiles=profiles)
    if staple_line_id is not None and preparation.recipe is not None:
        preparation = PreparedRecipe(replace(preparation.recipe, recipe_ids=tuple(recipe_ids)), ())
    result = validate_meal(preparation.recipe, targets, policy=policy) if preparation.recipe else None
    dish_portions = None
    unscaled_baseline = None
    if (conversion == 'factor_one' and result is not None and not result.nutrition_passed
            and preparation.recipe is not None
            and result.baseline_estimate is not None and not result.baseline_estimate.missing):
        # User decisions 2026-11-02/03: when the unchanged menu misses the tolerance,
        # each dish is portioned to the participants' targets, 0.1–2.0 recipe
        # serving (whole dish, same factor on every line).  The tolerance
        # itself is not relaxed.
        from .portion_fit import FACTOR_RANGE, FACTOR_STEP, POLICY_VERSION, candidate_factors, scale_dishes
        line_dish = {row['recipe_ingredient_id']: row['recipe_id'] for row in rows.ingredients
                     if row['recipe_id'] != 'staple'}
        for factors in candidate_factors(preparation.recipe, line_dish, targets, policy):
            scaled = validate_meal(scale_dishes(preparation.recipe, line_dish, factors), targets, policy=policy)
            if scaled.nutrition_passed:
                unscaled_baseline = result.baseline_estimate
                result = scaled
                dish_portions = {'policy_version': POLICY_VERSION, 'range': list(FACTOR_RANGE), 'step': FACTOR_STEP,
                                 'factors': {rid: _decimal_text(f) for rid, f in sorted(factors.items())}}
                break
    if staple_snapshot is not None and result and result.final_recipe:
        rice_line = next((line for line in result.final_recipe.ingredients
                          if line.line_id == staple_line_id), None)
        if rice_line is not None:
            staple_snapshot = {**staple_snapshot, 'final_grams': str(rice_line.amount_g),
                               'total_n': str(targets.total_n)}
    line_nutrients = None
    display_fills: dict[str, list] = {}
    if result and result.final_recipe:
        # Display only: the same per-line conversion for all seven nutrients of the final menu.
        # Blank cells of the five display-only nutrients are filled the t11-t20-demo way.
        from .nutrient_fill import fill_record
        filled_records = (None if policy.line_nutrition_records is None
                          else {lid: fill_record(record) for lid, record in policy.line_nutrition_records.items()})
        everything = replace(policy, required_nutrients=ALL_NUTRIENTS, line_nutrition_records=filled_records)
        display_fills = {lid: list(record['source']['display_fills']) for lid, record in (filled_records or {}).items()
                         if (record.get('source') or {}).get('display_fills')}
        line_nutrients = {}
        for line in result.final_recipe.ingredients:
            one = calculate_nutrition(replace(result.final_recipe, ingredients=(line,)), policy=everything)
            line_nutrients[line.line_id] = dict(one.totals)
    return DatabaseMenuResult(targets, preparation, result, staple_snapshot, dish_portions, unscaled_baseline,
                              line_nutrients=line_nutrients, display_fills=display_fills)
