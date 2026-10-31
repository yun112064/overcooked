"""Consume backend T04 cooking results under the user's LLM-parameter policy.

T04/its caller performs model calls outside DB transactions and T10 controls
spend. This module uses parameters, never T04's rounded/float nutrition totals.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import hashlib
import json
import math

from .integration import InputIssue, RecipeLineProfile, RecipeRows, recipe_line_digest
from .service import CookingEvidence, NutritionPolicy
from .targets import PlanningInputError, exact


@dataclass(frozen=True)
class T04CookingResult:
    """Backend-created receipt binding a result to its actual request/version."""
    input_digest: str
    food_id: str
    weight_basis: str
    input_weight_g: str
    cooking_method: str
    cooking_context: Mapping
    source_version: str  # T04 data/code + estimator version, no credentials.
    result: Mapping
    nutrition_import_id: str | None = None
    nutrition_id: str | None = None
    nutrition_source_id: str | None = None


def _reported(value):
    # T04 reports int/float values. Preserve their reported decimal digits and
    # label estimates; don't feed its binary-float nutrition totals to T12.
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PlanningInputError("Nonfinite T04 parameter")
        value = str(value)
    return exact(value)


def _context(value: Mapping) -> dict[str, str | bool]:
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise PlanningInputError("Invalid cooking context")
        if isinstance(item, bool) or isinstance(item, str) and item:
            result[key] = item
        elif isinstance(item, (int, float, Decimal)) and not isinstance(item, bool):
            result[key] = str(_reported(item))
        else:
            raise PlanningInputError("Invalid cooking context")
    return result


def apply_t04_results(rows: RecipeRows, *, policy: NutritionPolicy,
                      profiles: Mapping[str, RecipeLineProfile], results: Mapping[str, T04CookingResult]):
    """Accept validated T04 LLM coefficients without a second human review.

    This authorizes only cooking coefficients, not FOOD/sample mappings,
    recipe/unit reviews, T03 rules, or final adoption.
    """
    changed = dict(profiles)
    evidence = dict(policy.approved_evidence)
    issues = []
    catalog = {}
    if policy.line_nutrition_records is None:
        try:
            with policy.catalog_path.open(encoding='utf-8') as stream:
                catalog = {item['ingredient_id']: item for item in json.load(stream, parse_float=Decimal)}
        except (OSError, ValueError, TypeError, KeyError):
            return policy, changed, (InputIssue('tfda_catalog_unavailable', ''),)
    recipes = {r['recipe_id']: r for r in rows.recipes}
    lines = {r['recipe_ingredient_id']: r for r in rows.ingredients}
    for lid, receipt in results.items():
        row = lines.get(lid)
        if row is None:
            issues.append(InputIssue('t04_result_for_unselected_line', '', lid))
            continue
        rid = row['recipe_id']
        digest = recipe_line_digest(recipes[rid], row)
        profile = profiles.get(lid)
        def bad(code):
            issues.append(InputIssue(code, rid, lid, 't04_result', digest))
        if profile is None:
            bad('reviewed_line_profile_missing')
            continue
        try:
            raw = receipt.result
            weight = profile.reviewed_weight_g if profile.reviewed_weight_g is not None else row.get('weight_g')
            if (receipt.input_digest != digest or receipt.food_id != row['food_id']
                    or not isinstance(receipt.cooking_method, str) or not receipt.cooking_method.strip()
                    or receipt.weight_basis != profile.weight_basis
                    or _reported(receipt.input_weight_g) != exact(weight)
                    or receipt.cooking_method != profile.cooking_method
                    or dict(receipt.cooking_context) != dict(profile.cooking_context or {})
                    or not receipt.source_version or raw.get('success') is not True
                    or raw.get('cooking_method') != receipt.cooking_method
                    or type(raw.get('is_estimated')) is not bool):
                bad('t04_result_input_mismatch_or_failed')
                continue
            weight_field = {'raw': 'raw_weight_g', 'cooked': 'cooked_weight_g'}.get(receipt.weight_basis)
            if weight_field is None or _reported(raw.get(weight_field)) != _reported(receipt.input_weight_g):
                raise PlanningInputError('T04 result is for another weight')
            context = _context(receipt.cooking_context)
            context['cooking_method'] = receipt.cooking_method
            sample = policy.sample_for(lid, row['food_id'])
            if not isinstance(sample, str) or not sample:
                raise PlanningInputError('T04 sample mapping missing')
            sample_record = (policy.line_nutrition_records.get(lid, {}) if policy.line_nutrition_records is not None
                             else catalog.get(sample, {}))
            if policy.line_nutrition_records is not None:
                source_snapshot = sample_record.get('source', {})
                if (receipt.nutrition_import_id != source_snapshot.get('import_id')
                        or receipt.nutrition_id != source_snapshot.get('nutrition_id')
                        or receipt.nutrition_source_id != source_snapshot.get('nutrition_source_id')):
                    bad('t04_import_snapshot_mismatch')
                    continue
            sample_state = sample_record.get('state')
            retention = {}
            for nutrient in policy.required_nutrients:
                item = raw.get('nutrient_sources', {}).get(nutrient, {})
                source = item.get('source', {})
                if source.get('provider') != 'TFDA' or source.get('source_id') != sample:
                    raise PlanningInputError('T04 sample does not match T12 policy')
                if item.get('basis') == 'raw_tfda_llm_estimate':
                    if sample_state != 'raw':
                        raise PlanningInputError('Raw T04 composition has a cooked T12 sample')
                    factor = _reported(item.get('retention_factor'))
                    if not 0 <= factor <= 1:
                        raise PlanningInputError('Invalid retention')
                    retention[nutrient] = factor
                elif item.get('basis') != 'cooked_tfda' or sample_state != 'cooked':
                    raise PlanningInputError('Unknown nutrition basis')
            estimate = raw.get('llm_estimate') or {}
            if estimate and estimate.get('success') is not True:
                raise PlanningInputError('Failed T04 estimate')
            refs = raw.get('reference_ids', [])
            assumptions = raw.get('assumptions', [])
            if (not isinstance(refs, list) or any(not isinstance(x, str) or not x for x in refs)
                    or not isinstance(assumptions, list)
                    or any(not isinstance(x, str) or not x for x in assumptions)):
                raise PlanningInputError('Invalid T04 provenance')
            if (retention or raw.get('yield_basis') == 'llm_estimated') and (
                    not raw['is_estimated'] or not assumptions):
                raise PlanningInputError('LLM parameters must retain estimated status and assumptions')
            factor = None
            basis = raw.get('yield_basis')
            if basis == 'llm_estimated':
                factor = _reported(estimate.get('yield_factor'))
                available = {r['reference_id'] for r in raw.get('yield_references', [])}
                if (not refs or not set(refs).issubset(available) or not assumptions
                        or not 0 < factor <= 10):
                    raise PlanningInputError('Unverifiable LLM yield')
            elif basis == 'usda_exact':
                factor = _reported(raw.get('cooking_yield_pct')) / 100
                if not refs or factor <= 0:
                    raise PlanningInputError('Invalid USDA yield')
            elif basis == 'measured':
                raw_weight, cooked_weight = raw.get('raw_weight_g'), raw.get('cooked_weight_g')
                if raw_weight is not None and cooked_weight is not None:
                    factor = _reported(cooked_weight) / _reported(raw_weight)
                    if factor <= 0:
                        raise PlanningInputError('Invalid measured yield')
            elif basis is not None:
                raise PlanningInputError('Unknown yield basis')
            fingerprint = hashlib.sha256(json.dumps(asdict(receipt), sort_keys=True, default=str,
                ensure_ascii=False).encode()).hexdigest()
            eid = 't04-' + fingerprint
            evidence[eid] = CookingEvidence(
                evidence_id=eid, sample_id=sample, cooking_method=receipt.cooking_method,
                yield_factor=factor, retention=retention,
                source='T04_LLM' if raw['is_estimated'] else 'T04',
                source_version=receipt.source_version, conditions=context,
                is_estimated=raw['is_estimated'], reference_ids=tuple(refs), assumptions=tuple(assumptions))
            changed[lid] = replace(profile, evidence_id=eid, cooking_context=deepcopy(context))
        except (PlanningInputError, KeyError, TypeError, AttributeError, ZeroDivisionError):
            bad('t04_parameters_missing_invalid_or_sample_mismatch')
    return replace(policy, approved_evidence=evidence), changed, tuple(issues)
