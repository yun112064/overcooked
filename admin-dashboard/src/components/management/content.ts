import type { Content, EditableDomain, Line, Measurement } from '../../api/management'

const list = (value: string) => value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)

export function unknownMeasurement(reason = '來源未提供'): Measurement {
  return { status: 'unknown', value: null, unknown_reason: reason, evidence_ids: [] }
}

export function newLine(): Line {
  return {
    line_id: crypto.randomUUID(), food_id: null, ingredient_version_id: null, canonical_name: '', raw_text: '',
    amount: unknownMeasurement(), unit: null, weight_g: unknownMeasurement(), weight_basis: 'whole_source_recipe_before_cooking',
  }
}

export function blankContent(domain: EditableDomain): Content {
  const allergens = { aliases: [], declared_allergen_ids: null, sources: [] }
  if (domain === 'ingredient') return { ...allergens, food_id: null, canonical_name: '' }
  if (domain === 'recipe') return {
    ...allergens, logical_recipe_id: null, name: '', standard_dish_id: null, category: '', cuisine: '', meal_type: '',
    servings: { count: null, basis: 'portions', raw_text: '', portions_per_batch: null, evidence_ids: [] },
    ingredients: [newLine()], steps: [{ step_no: 1, instruction: '' }],
  }
  return {
    correction_kind: 'product_binding', food_id: '', ingredient_version_id: '', ingredient_publication_id: '', legacy_recipe_release_id: '',
    provider: 'manual_evidence', product_id: '', product_name: '', snapshot_id: null, price_twd: null, package_mass_g: null,
    observed_at: null, valid_from: null, valid_until: null, availability: 'unknown', region_condition: null,
    store_condition: null, promotion_condition: null, sources: [],
  }
}

export function cleanLists(content: Content): Content {
  if (!('aliases' in content)) return content
  return { ...content, aliases: list(content.aliases.join('\n')),
    declared_allergen_ids: content.declared_allergen_ids === null ? null : list(content.declared_allergen_ids.join('\n')) }
}
