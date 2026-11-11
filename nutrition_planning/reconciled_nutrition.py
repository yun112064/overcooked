"""Explicit, read-only factor-one source resolution for release reconciliation.

This module is not enabled by any runtime entry point. Callers must separately
verify their release manifest and reconcile SQL review decisions before using
these records for acceptance. Resolving a source here does not approve a SQL
mapping, change a rejection, or establish recipe/import compatibility.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from sqlalchemy.engine import Connection

from .imported_nutrition import (
    ImportedNutritionData, load_imported_nutrition, resolve_imported_selections,
)
from .integration import (
    FACTOR_ONE_ASSUMPTION, SUPPLEMENTABLE_CODES, InputIssue, RecipeRows,
    _apply_fill, _supplement_record, recipe_level_profiles,
)
from .nutrition_mapping import NutritionMapping
from .nutrition_supplement import NutritionSupplements
from .targets import PlanningInputError


def resolve_reconciled_nutrition(
    db: Connection,
    rows: RecipeRows,
    *,
    import_id: str,
    mappings: Mapping[str, NutritionMapping],
    supplements: NutritionSupplements,
) -> ImportedNutritionData:
    """Resolve every line without external models, defaults, or database writes.

    Successful lines are retained alongside unresolved-line issues for auditing.
    The caller must treat any issues as incomplete, not silently drop those
    ingredients. Supplements resolve only the existing supplementable selection
    failures; excluded, ambiguous or invalid sources never take that shortcut.
    """
    if not isinstance(import_id, str) or not import_id.strip():
        raise PlanningInputError('An explicit nutrition import_id is required.')
    if not isinstance(mappings, Mapping) or any(
        not isinstance(value, NutritionMapping) or key != value.food_id
        for key, value in mappings.items()
    ):
        raise PlanningInputError('Explicit parsed nutrition mappings are required.')
    if not isinstance(supplements, NutritionSupplements):
        raise PlanningInputError('Explicit parsed nutrition supplements are required.')
    lines = {line['recipe_ingredient_id']: line for line in rows.ingredients}
    if len(lines) != len(rows.ingredients):
        raise PlanningInputError('Recipe ingredient IDs must be unique.')

    selections, selection_issues = resolve_imported_selections(
        db, rows, import_id=import_id, selections={},
        profiles=recipe_level_profiles(rows), conversion='factor_one',
        mappings=mappings,
        # None as the resolver itself invokes the legacy external LLM. An
        # explicit callback declining every ambiguity keeps this path offline.
        variant_resolver=lambda request: None,
    )
    issues = []
    supplemental_records = {}
    for issue in selection_issues:
        if issue.code == 'llm_variant_decision_invalid':
            issue = replace(issue, code='nutrition_source_ambiguous', field='nutrition')
        line = lines.get(issue.line_id)
        supplement = (supplements.values.get(line['food_id'])
                      if line is not None and issue.code in SUPPLEMENTABLE_CODES else None)
        if supplement is None:
            issues.append(issue)
        else:
            supplemental_records[issue.line_id] = _supplement_record(line, supplement)

    # The imported adapter rejects extra selections. Keep rows and selections
    # in lockstep, and still call it when empty to validate the pinned import.
    selected_lines = tuple(line for line in rows.ingredients
                           if line['recipe_ingredient_id'] in selections
                           and line['recipe_ingredient_id'] not in supplemental_records)
    selected_ids = {line['recipe_ingredient_id'] for line in selected_lines}
    loaded = load_imported_nutrition(
        db, replace(rows, ingredients=selected_lines), import_id=import_id,
        selections={key: value for key, value in selections.items() if key in selected_ids},
        allow_usda=True,
    )
    global_issues = [issue for issue in loaded.issues if issue.line_id is None]
    if global_issues:
        # A broken/absent import cannot be masked by otherwise valid supplements.
        return ImportedNutritionData({}, tuple(
            replace(issue, recipe_id=line['recipe_id'], line_id=line['recipe_ingredient_id'])
            for line in rows.ingredients for issue in global_issues
        ) or tuple(global_issues))

    issues.extend(loaded.issues)
    records = {lid: _apply_fill(record, supplements.fills)
               for lid, record in loaded.line_records.items()}
    records.update(supplemental_records)
    # Mirror the existing preview's raw-weight assumption, retaining every
    # source field and leaving cooked/as-eaten records on their existing basis.
    records = {
        lid: {**record, 'source': {
            **record['source'],
            **({'conversion_assumption': FACTOR_ONE_ASSUMPTION}
               if record.get('state') == 'raw' else {}),
        }}
        for lid, record in records.items()
    }
    return ImportedNutritionData(records, tuple(issues))
