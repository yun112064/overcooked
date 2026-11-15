import type { CatalogDish } from '../api/types'

export const MAX_DISHES = 10

/** 只從已篩選的食譜庫清單抽一道，既有選取與後端標示的衝突仍有效。 */
export function randomDish(shown: readonly CatalogDish[], chosen: readonly string[], random = Math.random): string | undefined {
  const selected = new Set(chosen)
  if (selected.size >= MAX_DISHES) return undefined
  const candidates = [...new Set(shown.filter(dish => !dish.conflicts.length && !selected.has(dish.dish_name)).map(dish => dish.dish_name))]
  if (!candidates.length) return undefined
  return candidates[Math.floor(random() * candidates.length)]
}
