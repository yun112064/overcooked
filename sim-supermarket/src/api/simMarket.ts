export type MarketProduct = {
  food_id: string
  name: string
  category: string
  package_grams: number | null
  package_ml?: number | null
  price_cents: number
  image_url?: string | null
  product_name?: string | null
  product_url?: string | null
  quote_snapshot_id?: number
  source_url?: string
  source?: string
  queried_at?: string
  region_condition?: string | null
  store_condition?: string | null
  promotion_description?: string | null
}

export type MarketCatalog = {
  real_checkout?: boolean
  recipe_release_id: string | null
  products: MarketProduct[]
  unavailable_ingredients?: { food_id: string; name: string; category: string; reason: string }[]
  ingredient_count?: number
}

type MarketShortages = {
  items: { food_id: string; name: string; shortage_grams: string | null; shortage_ml?: string | null; recipe_release_id?: string | null }[]
  unquantified_items?: { food_id: string; name: string }[]
}

export type MarketPlan = {
  id: string; status: string; meal_date: string; result_version?: number | null
  dishes: { dish_name: string }[]; snapshot_available: boolean
  prices?: MarketShortages
  snapshot?: {
    result?: { dishes?: { recipe_release_id?: string | null }[] }
    market_shortages?: MarketShortages & { recipe_release_id: string | null }
  }
}

export type MarketCart = { quantities: Record<string, number>; orderId: string | null }
export type UnavailableIngredient = { foodId: string; name: string; grams?: string | null; ml?: string | null; reason: string }

export const MAX_PACKS = 1_000_000

export function marketPath(page: 'catalog' | 'checkout') {
  return page === 'checkout' ? '/checkout' : '/'
}

type ExactQuantity = { numerator: bigint; denominator: bigint }

function exactQuantity(value: string | null | undefined): ExactQuantity | null {
  if (value == null) return null
  const ratio = /^(\d{1,128})\/([1-9]\d{0,127})$/.exec(value)
  if (ratio) return { numerator: BigInt(ratio[1]), denominator: BigInt(ratio[2]) }
  // Backend Decimal quantities can contain 28+ decimal places. Keep every
  // digit for package rounding, bounded like the existing rational parser.
  const match = /^(\d{1,16})(?:\.(\d{1,128}))?(?:[eE]([+-]?\d{1,2}))?$/.exec(value)
  if (!match) return null
  const fraction = match[2] ?? ''
  const scale = fraction.length - Number(match[3] ?? 0)
  return {
    numerator: BigInt(match[1] + fraction) * 10n ** BigInt(Math.max(0, -scale)),
    denominator: 10n ** BigInt(Math.max(0, scale)),
  }
}

function addQuantity(left: ExactQuantity, right: ExactQuantity): ExactQuantity {
  const numerator = left.numerator * right.denominator + right.numerator * left.denominator
  const denominator = left.denominator * right.denominator
  let divisor = numerator
  let remainder = denominator
  while (remainder !== 0n) [divisor, remainder] = [remainder, divisor % remainder]
  return { numerator: numerator / divisor, denominator: denominator / divisor }
}

function exactString(quantity: ExactQuantity): string {
  return quantity.denominator === 1n ? String(quantity.numerator) : `${quantity.numerator}/${quantity.denominator}`
}

/** Round only after all requirements for the same product have been summed. */
export function packCount(amount: string, packageSize: number): number | null {
  if (!Number.isSafeInteger(packageSize) || packageSize <= 0) return null
  const quantity = exactQuantity(amount)
  if (quantity === null) return null
  const size = BigInt(packageSize) * quantity.denominator
  const packs = (quantity.numerator + size - 1n) / size
  return packs <= MAX_PACKS ? Number(packs) : null
}

export function packageSpec(product: MarketProduct): { quantity: number; unit: 'g' | 'mL' } | null {
  const mass = product.package_grams
  const volume = product.package_ml
  if (mass != null && volume != null) return null
  const quantity = volume ?? mass
  if (quantity == null || !Number.isSafeInteger(quantity) || quantity <= 0) return null
  return { quantity, unit: volume != null ? 'mL' : 'g' }
}

export function cartStorageKey(scope: (string | null | undefined)[], catalog: MarketCatalog): string {
  const packages = catalog.products.map(p => [p.food_id, p.package_grams, p.package_ml ?? null, p.price_cents])
    .sort((a, b) => String(a[0]).localeCompare(String(b[0])))
  return `sim-market:v2:${JSON.stringify([scope, catalog.recipe_release_id, packages])}`
}

export function prepareCart(plan: MarketPlan | undefined, catalog: MarketCatalog) {
  const products = new Map(catalog.products.map((product) => [product.food_id, product]))
  const preview = plan?.snapshot?.market_shortages
  const shortagesSource: MarketShortages | undefined = preview ?? plan?.prices
  const releases = (preview ? [preview.recipe_release_id] : [
    ...(plan?.snapshot?.result?.dishes ?? []).map((dish) => dish.recipe_release_id),
    ...(plan?.prices?.items ?? []).map((item) => item.recipe_release_id),
  ]).filter(Boolean)
  const releaseIssue = plan && releases.length === 0 ? '採用餐單未記錄可核對的 T01 版本'
    : plan && catalog.recipe_release_id === null ? '目前沒有可用的 T01 商品目錄'
      : releases.some((release) => release !== catalog.recipe_release_id) ? '餐單與商品目錄的 T01 版本不同' : null
  const unavailable: UnavailableIngredient[] = (shortagesSource?.unquantified_items ?? []).map((item) => ({
    foodId: item.food_id, name: item.name, reason: '採用餐單未記錄可換算的用量',
  }))
  const blocked = new Set(unavailable.map((item) => item.foodId))
  const shortages = new Map<string, string>()
  const totals = new Map<string, ExactQuantity>()
  const quantities: Record<string, number> = {}
  for (const item of shortagesSource?.items ?? []) {
    if (blocked.has(item.food_id)) continue
    const product = products.get(item.food_id)
    const spec = product && packageSpec(product)
    const raw = spec?.unit === 'mL' ? item.shortage_ml : item.shortage_grams
    const quantity = exactQuantity(raw)
    if (quantity?.numerator === 0n) continue
    if (!product || releaseIssue || !spec || quantity === null) {
      unavailable.push({
        foodId: item.food_id, name: item.name, grams: item.shortage_grams, ml: item.shortage_ml,
        reason: releaseIssue ?? (!product ? '模擬超市尚未提供此食材' : '缺量與商品包裝單位無法換算，請確認用量或換算依據'),
      })
      blocked.add(item.food_id)
      continue
    }
    totals.set(item.food_id, addQuantity(totals.get(item.food_id) ?? { numerator: 0n, denominator: 1n }, quantity))
  }
  for (const [foodId, total] of totals) {
    if (blocked.has(foodId)) continue
    const product = products.get(foodId)!
    const spec = packageSpec(product)!
    const amount = exactString(total)
    const packs = packCount(amount, spec.quantity)
    if (packs === null) {
      unavailable.push({ foodId, name: product.name, reason: '需求包數超過模擬購物車上限' })
      continue
    }
    quantities[foodId] = packs
    shortages.set(foodId, `${amount} ${spec.unit}`)
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
