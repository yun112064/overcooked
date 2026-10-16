"""One cooking pipeline for both legacy entry points and recipe aggregation."""
import json
from pathlib import Path

import cooking_yield_service as yields
from llm_cooking_estimator import NUTRIENTS, estimate_cooking, finite_number

MAPPING_PATH = Path(__file__).parent / 'data' / 'tfda_cooked_mapping.json'
with MAPPING_PATH.open(encoding='utf-8') as stream:
    COOKED_MAPPINGS = json.load(stream)['mappings']


def cooked_metadata_for(ingredient_id):
    matches = [m for m in COOKED_MAPPINGS if m['cooked_ingredient_id'] == ingredient_id]
    methods = {m['cooking_method'] for m in matches}
    return {'cooking_method': next(iter(methods))} if len(methods) == 1 else {}


def find_cooked(name, method, records, find_ingredient, context=None):
    """Only explicit links qualify; never infer equivalence from similar names."""
    context = context or {}
    normalized = yields.normalize_method(method)
    raw = find_ingredient(name)
    raw_id = raw.get('ingredient_id') if raw else None
    ids = {m['cooked_ingredient_id'] for m in COOKED_MAPPINGS
           if name in m['input_names'] and yields.normalize_method(m['cooking_method']) == normalized}
    candidates = []
    for record in records:
        if record.get('state') != 'cooked':
            continue
        explicit_link = (raw_id is not None and record.get('raw_ingredient_id') == raw_id)
        exact_name = name == record.get('standard_name') or name in record.get('aliases', [])
        method_matches = yields.normalize_method(record.get('cooking_method')) == normalized
        if record['ingredient_id'] in ids or (method_matches and (explicit_link or exact_name)):
            candidates.append(record)
    selected_id = context.get('tfda_cooked_id')
    if selected_id:
        candidates = [r for r in candidates if r['ingredient_id'] == selected_id]
    return candidates[0] if len(candidates) == 1 else None


def calculate_cooking(name, weight_g, method, records, find_ingredient, *,
                      cooked_weight_g=None, weight_basis='raw', cooking_context=None, estimator=None):
    empty = {key: None for key in NUTRIENTS}
    result = {'success': False, 'ingredient': name, 'nutrition': empty.copy(),
              'nutrition_complete': False, 'cooking_adjustment_complete': False,
              'nutrition_basis': None, 'is_estimated': False,
              'raw_weight_g': None, 'cooked_weight_g': None,
              'cooking_method': method, 'yield_basis': None, 'yield_source': None,
              'cooking_yield_pct': None, 'nutrient_sources': {},
              'reference_ids': [], 'assumptions': [], 'missing_fields': list(NUTRIENTS)}
    if (not isinstance(name, str) or not name.strip()
            or not isinstance(method, str) or not method.strip()
            or not finite_number(weight_g, positive=True)
            or weight_basis not in ('raw', 'cooked')
            or (cooked_weight_g is not None and not finite_number(cooked_weight_g, positive=True))
            or (cooking_context is not None and not isinstance(cooking_context, dict))):
        return {**result, 'error': 'invalid_cooking_input'}
    if weight_basis == 'cooked' and cooked_weight_g is not None and cooked_weight_g != weight_g:
        return {**result, 'error': 'conflicting_cooked_weights'}
    context = dict(cooking_context or {})
    raw = find_ingredient(name)
    cooked = find_cooked(name, method, records, find_ingredient, context)
    if raw and raw.get('state') == 'cooked' and weight_basis != 'cooked':
        return {**result, 'error': 'cooked_food_requires_cooked_weight_basis'}
    # A known cooked food cannot be silently treated as raw composition.
    raw = raw if raw and raw.get('state') == 'raw' else None
    if raw is None and cooked is None:
        return {**result, 'error': 'ingredient_not_found'}
    result['success'] = True
    raw_weight = weight_g if weight_basis == 'raw' else None
    measured = weight_g if weight_basis == 'cooked' else cooked_weight_g
    result.update(raw_weight_g=raw_weight, cooked_weight_g=measured)
    if raw and raw_weight:
        result['raw_reference'] = {
            'source': raw['source'], 'nutrition': {
                key: value * raw_weight / 100 if finite_number(value := raw['nutrition_per_100g'].get(key)) else None
                for key in NUTRIENTS}}
    references = yields.get_yield_references(name, method)
    exact = [r for r in references if r['match_type'] == 'exact']
    factor = None
    if measured is not None:
        factor = measured / raw_weight if raw_weight else None
        result.update(yield_basis='measured', yield_source={'provider': 'input'})
    elif len(exact) == 1:
        factor = exact[0]['cooking_yield_pct'] / 100
        result.update(yield_basis='usda_exact', yield_source=exact[0]['source'],
                      reference_ids=[exact[0]['reference_id']])

    needs_composition = cooked is None or any(not finite_number(cooked['nutrition_per_100g'].get(k)) for k in NUTRIENTS)
    needs_yield = measured is None and factor is None
    estimate = None
    if needs_yield or (needs_composition and raw is not None and raw_weight is not None):
        payload = {'ingredient_name': name, 'raw_weight_g': raw_weight,
                   'cooked_weight_g': measured, 'cooking_method': method,
                   'cooking_context': context, 'yield_references': references,
                   'raw_nutrition_per_100g': {k: v if finite_number(v := raw['nutrition_per_100g'].get(k)) else None
                                              for k in NUTRIENTS} if raw else None,
                   'raw_source': raw['source'] if raw else None,
                   'tfda_cooked_nutrition_per_100g': cooked['nutrition_per_100g'] if cooked else None,
                   'fixed_yield_factor': factor,
                   'additions_policy': 'oil, salt, sauce are separate recipe rows; never add them here'}
        estimate = estimate_cooking(payload, estimator)
        if estimate['success']:
            result.update(assumptions=estimate['assumptions'],
                          missing_information=estimate['missing_information'],
                          llm_estimate=estimate,
                          yield_references=references)
            if needs_yield and estimate['yield_factor'] is not None:
                factor = estimate['yield_factor']
                result.update(yield_basis='llm_estimated', yield_source={'provider': 'LLM', 'references': 'USDA'},
                              reference_ids=estimate['reference_ids'])
        else:
            result['estimation_error'] = estimate['error']
    if measured is None and factor is not None and raw_weight is not None:
        measured = raw_weight * factor
        result['cooked_weight_g'] = measured
    result['cooking_yield_pct'] = factor * 100 if factor is not None else None

    for key in NUTRIENTS:
        cooked_value = cooked['nutrition_per_100g'].get(key) if cooked else None
        if not finite_number(cooked_value):
            cooked_value = None
        if cooked_value is not None and measured is not None:
            result['nutrition'][key] = cooked_value * measured / 100
            result['nutrient_sources'][key] = {'basis': 'cooked_tfda', 'source': cooked['source'],
                                               'is_estimated': result['yield_basis'] == 'llm_estimated'}
        elif (cooked_value is None and raw_weight is not None and raw is not None
              and estimate and estimate['success']):
            raw_value = raw['nutrition_per_100g'].get(key)
            retention = estimate['nutrient_retention_factors'].get(key)
            if finite_number(raw_value) and retention is not None:
                result['nutrition'][key] = raw_value * raw_weight / 100 * retention
                result['nutrient_sources'][key] = {'basis': 'raw_tfda_llm_estimate', 'source': raw['source'],
                                                   'retention_factor': retention, 'is_estimated': True}
    bases = {v['basis'] for v in result['nutrient_sources'].values()}
    result['nutrition_basis'] = ('cooked_tfda_with_llm_estimate' if len(bases) > 1
                                 else next(iter(bases), None))
    result['nutrition_source'] = cooked['source'] if cooked else (raw['source'] if raw else None)
    result['is_estimated'] = (result['yield_basis'] == 'llm_estimated'
                              or any(v['is_estimated'] for v in result['nutrient_sources'].values()))
    result['missing_fields'] = [k for k, v in result['nutrition'].items() if v is None]
    result['nutrition_complete'] = not result['missing_fields']
    result['cooking_adjustment_complete'] = result['nutrition_complete'] and measured is not None
    result['missing_reason'] = None if result['cooking_adjustment_complete'] else (
        result.get('estimation_error') or 'cooked_weight_or_nutrients_unavailable')
    return result


def calculate_recipe(recipe_name, recipe_ingredients, cooking_calculator, raw_calculator,
                     find_ingredient, *, servings=1, estimator=None, cooked_weight_g=None):
    if (not isinstance(recipe_ingredients, list) or not recipe_ingredients
            or not finite_number(servings, positive=True)
            or (cooked_weight_g is not None and not finite_number(cooked_weight_g, positive=True))):
        return {'success': False, 'error': 'invalid_recipe_input', 'nutrition_complete': False,
                'cooking_adjustment_complete': False, 'total_nutrition': {k: None for k in NUTRIENTS}}
    results, failed, incomplete = [], [], []
    for item in recipe_ingredients:
        if not isinstance(item, dict):
            failed.append({'ingredient': None, 'error': 'invalid_ingredient_input'})
            continue
        name, weight = item.get('name'), item.get('weight_g')
        retained = item.get('retained_fraction', 1)
        if not finite_number(retained) or retained > 1:
            failed.append({'ingredient': name, 'error': 'invalid_retained_fraction'})
            continue
        if 'cooking_method' in item and item['cooking_method'] is not None:
            row = cooking_calculator(name, weight, item['cooking_method'],
                cooked_weight_g=item.get('cooked_weight_g'), weight_basis=item.get('weight_basis', 'raw'),
                cooking_context=item.get('cooking_context'), estimator=estimator)
        else:
            row = raw_calculator(name, weight, round_result=False)
            record = find_ingredient(name) if isinstance(name, str) else None
            if item.get('weight_basis', 'raw') not in ('raw', 'cooked'):
                row = {'success': False, 'error': 'invalid_weight_basis'}
            elif record and record.get('state') == 'cooked' and item.get('weight_basis') != 'cooked':
                row = {'success': False, 'error': 'cooked_food_requires_cooked_weight_basis'}
            elif record and record.get('state') != 'cooked' and item.get('weight_basis') == 'cooked':
                row = {'success': False, 'error': 'cooked_composition_required'}
            as_is = item.get('consumed_as_is', False)
            if not isinstance(as_is, bool):
                row = {'success': False, 'error': 'invalid_consumed_as_is'}
            ready = bool(record and record.get('state') == 'cooked') or as_is is True
            basis = 'cooked_tfda' if record and record.get('state') == 'cooked' else 'raw_tfda'
            row.update(ingredient=name, nutrition_basis=basis,
                       cooking_adjustment_complete=ready and row.get('nutrition_complete', False), is_estimated=False,
                       missing_reason=None if ready else 'no_cooking_method; composition used as supplied',
                       nutrient_sources={k: {'basis': basis, 'source': record['source'], 'is_estimated': False}
                                         for k, v in row.get('nutrition', {}).items() if v is not None} if record else {},
                       cooked_weight_g=weight if ready and row.get('success') else None)
            if row.get('success') and not ready:
                row['raw_reference'] = {'source': record['source'], 'nutrition': row['nutrition'].copy()}
                row.update(nutrition={k: None for k in NUTRIENTS}, nutrition_complete=False,
                           missing_fields=list(NUTRIENTS), nutrient_sources={}, nutrition_basis=None)
        row['retained_fraction'] = retained
        if row.get('success'):
            row['nutrition'] = {k: v * retained if v is not None else None
                                for k, v in row['nutrition'].items()}
            if row.get('cooked_weight_g') is not None:
                row['cooked_weight_g'] *= retained
        results.append(row)
        if not row.get('success'):
            failed.append({'ingredient': name, 'error': row.get('error')})
        # Whole-dish measured mass is sufficient for recipe-level per-100g
        # calculation. It does not fill unknown individual weights or nutrients.
        dish_mass_resolves_weight = (cooked_weight_g is not None
            and row.get('success') and row.get('nutrition_complete'))
        if not row.get('cooking_adjustment_complete') and not dish_mass_resolves_weight:
            incomplete.append({'ingredient': name, 'reason': row.get('missing_reason') or row.get('error')})
    partial = {k: sum(r.get('nutrition', {}).get(k) or 0 for r in results if r.get('success')) for k in NUTRIENTS}
    missing = {k: [r['ingredient'] for r in results if r.get('success') and r.get('nutrition', {}).get(k) is None]
               for k in NUTRIENTS}
    total = {k: None if failed or missing[k] else partial[k] for k in NUTRIENTS}
    if cooked_weight_g is None and len(results) == len(recipe_ingredients) and not failed:
        weights = [r.get('cooked_weight_g') for r in results]
        if all(w is not None for w in weights):
            cooked_weight_g = sum(weights)
    complete = not failed and all(v is not None for v in total.values())
    return {'success': not failed, 'recipe_name': recipe_name, 'ingredients': results,
            'total_nutrition': {k: round(v, 2) if v is not None else None for k, v in total.items()},
            'partial_nutrition_totals': {k: round(v, 2) for k, v in partial.items()},
            'nutrition_complete': complete, 'cooking_adjustment_complete': not incomplete and not failed,
            'missing_nutrients': {k: names for k, names in missing.items() if names},
            'failed_ingredients': failed, 'cooking_incomplete': incomplete,
            'is_estimated': any(r.get('is_estimated', False) for r in results),
            'servings': servings, 'cooked_weight_g': cooked_weight_g,
            'per_serving_nutrition': {k: round(v / servings, 2) if v is not None else None for k, v in total.items()},
            'nutrition_per_100g': {k: round(v / cooked_weight_g * 100, 2)
                                   if v is not None and cooked_weight_g else None for k, v in total.items()}}
