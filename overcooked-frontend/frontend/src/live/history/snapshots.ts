import type { GenerationPlanDetail, MealPlanDetail, MealResult, PriceItem } from '../api/types'

type SavedCost = Omit<MealResult['prices'], 'items' | 'unquantified_items'> & {
  lines: (Pick<PriceItem, 'food_id' | 'shortage_grams' | 'shortage_ml' | 'price_status' | 'packs' | 'purchase_price' | 'cost' | 'missing_reason'> & {
    quote?: Partial<PriceItem> | null
  })[]
  unquantified_food_ids?: string[]
}
export type SavedLegacyPlan = MealPlanDetail & { snapshot: MealPlanDetail['snapshot'] & { cost?: SavedCost } }
export type SavedGenerationPlan = GenerationPlanDetail & { staple?: MealResult['staple'] }
export type SavedPlan = SavedLegacyPlan | SavedGenerationPlan

/** 只轉換已保存的 T13 欄位，不重新查價，不補入目前的食譜或成員資料。 */
export function savedPrices(snapshot: SavedLegacyPlan['snapshot']): MealResult['prices'] | undefined {
  const cost = snapshot.cost
  if (!cost) return undefined
  const names = new Map((snapshot.dishes ?? []).flatMap(dish => dish.ingredients.map(i => [i.food_id, i.name] as const)))
  const unquantified = (cost.unquantified_food_ids ?? []).map(id => ({ food_id: id, name: names.get(id) ?? id }))
  return { ...cost,
    ...(unquantified.length ? { price_complete: false, purchase_total: null, cost_total: null } : {}),
    unquantified_items: unquantified,
    items: (cost.lines ?? []).filter(line => ['known', 'unknown'].includes(line.price_status)).map(line => ({
      food_id: line.food_id, name: names.get(line.food_id) ?? line.food_id,
      shortage_grams: line.shortage_grams, shortage_ml: line.shortage_ml, price_status: line.price_status,
      packs: line.packs, purchase_price: line.purchase_price, cost: line.cost, missing_reason: line.missing_reason,
      retailer: line.quote?.retailer ?? null, source_name: line.quote?.source_name ?? null,
      source: line.quote?.source ?? null, product_name: line.quote?.product_name ?? null,
      product_url: line.quote?.product_url ?? null, package_description: line.quote?.package_description ?? null,
      queried_at: line.quote?.queried_at ?? null, valid_until: line.quote?.valid_until ?? null,
    })),
  }
}
