import type { GenerationPlanDetail, MealPlanSummary } from './types'

export type MarketProduct = {
  food_id: string
  name: string
  category: string
  package_grams: number
  price_cents: number
}

export type MarketCatalog = {
  recipe_release_id: string | null
  products: MarketProduct[]
}

export type MarketPlan = GenerationPlanDetail & Pick<MealPlanSummary, 'status'> & {
  snapshot?: { result?: { dishes?: { recipe_release_id?: string | null }[] } }
}

export type MarketCart = { quantities: Record<string, number>; orderId: string | null }
export type UnavailableIngredient = { foodId: string; name: string; grams?: string; reason: string }

export const MAX_PACKS = 1_000_000

export function marketPath(page: 'catalog' | 'checkout', planId: string | null = null) {
  const path = page === 'checkout' ? '/sim-market/checkout' : '/sim-market'
  return planId ? `${path}?plan=${encodeURIComponent(planId)}` : path
}

/** Calculate ceil(grams / pack size) exactly, including decimal and exponent strings from Python Decimal. */
export function packCount(grams: string, packageGrams: number): number | null {
  const match = /^(\d{1,16})(?:\.(\d{1,16}))?(?:[eE]([+-]?\d{1,2}))?$/.exec(grams)
  if (!match || !Number.isSafeInteger(packageGrams) || packageGrams <= 0) return null
  const fraction = match[2] ?? ''
  const scale = fraction.length - Number(match[3] ?? 0)
  const quantity = BigInt(match[1] + fraction) * 10n ** BigInt(Math.max(0, -scale))
  const size = BigInt(packageGrams) * 10n ** BigInt(Math.max(0, scale))
  const packs = (quantity + size - 1n) / size
  return packs <= MAX_PACKS ? Number(packs) : null
}

export function prepareCart(plan: MarketPlan | undefined, catalog: MarketCatalog) {
  const products = new Map(catalog.products.map((product) => [product.food_id, product]))
  const prices = plan?.prices
  const releases = [
    ...(plan?.snapshot?.result?.dishes ?? []).map((dish) => dish.recipe_release_id),
    ...(prices?.items ?? []).map((item) => item.recipe_release_id),
  ].filter(Boolean)
  const releaseIssue = plan && releases.length === 0 ? '採用餐單未記錄可核對的 T01 版本'
    : plan && catalog.recipe_release_id === null ? '目前沒有可用的 T01 商品目錄'
      : releases.some((release) => release !== catalog.recipe_release_id) ? '餐單與商品目錄的 T01 版本不同' : null
  const unavailable: UnavailableIngredient[] = (prices?.unquantified_items ?? []).map((item) => ({
    foodId: item.food_id, name: item.name, reason: '採用餐單未記錄可換算的用量',
  }))
  const unknownQuantities = new Set(unavailable.map((item) => item.foodId))
  const shortages = new Map<string, string>()
  const quantities: Record<string, number> = {}
  for (const item of prices?.items ?? []) {
    if (unknownQuantities.has(item.food_id)) continue
    const product = products.get(item.food_id)
    const packs = packCount(item.shortage_grams, product?.package_grams ?? 1)
    if (packs === 0) continue
    if (!product || releaseIssue || packs === null) {
      unavailable.push({
        foodId: item.food_id, name: item.name, grams: item.shortage_grams,
        reason: releaseIssue ?? (!product ? '模擬超市尚未提供此食材' : '缺量或包裝規格無法換算成包數'),
      })
      continue
    }
    quantities[item.food_id] = packs
    shortages.set(item.food_id, item.shortage_grams)
  }
  return { quantities, shortages, unavailable, releaseIssue }
}

export function readCart(key: string, products: MarketProduct[], quantities: MarketCart['quantities']): MarketCart {
  try {
    const saved = sessionStorage.getItem(key)
    if (saved !== null) {
      const parsed = JSON.parse(saved) as Partial<MarketCart> | null
      if (parsed?.quantities) {
        const validEntries = products.flatMap(({ food_id }) => {
          const quantity = parsed.quantities?.[food_id]
          return typeof quantity === 'number' && Number.isSafeInteger(quantity) && quantity > 0 && quantity <= MAX_PACKS
            ? [[food_id, quantity] as const] : []
        })
        return { quantities: Object.fromEntries(validEntries),
          orderId: typeof parsed.orderId === 'string' && /^SIM-[0-9A-F]{8}$/.test(parsed.orderId) ? parsed.orderId : null }
      }
    }
  } catch {
    // The cart still works in memory when storage is unavailable.
  }
  return { quantities, orderId: null }
}

export function saveCart(key: string, cart: MarketCart) {
  try {
    sessionStorage.setItem(key, JSON.stringify(cart))
  } catch {
    // Browsing in this page still works when storage is unavailable.
  }
}
