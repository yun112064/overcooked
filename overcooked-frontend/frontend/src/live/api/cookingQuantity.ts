type Quantity = {
  display_amount?: string | number | null; display_unit?: string | null; display_text?: string | null
  display_grams?: string | number | null; whole_meal_grams?: string | number | null; grams?: string | number | null
}

function numeric(value: string | number | null | undefined) {
  if (value == null || !String(value).trim()) return null
  const [a, b] = String(value).split('/')
  const n = b === undefined ? Number(a) : Number(a) / Number(b)
  return Number.isFinite(n) && n >= 0 ? n : null
}

/** Display only: ranges never change the source quantity or calculation. */
export function cookingQuantity(i: Quantity): { label: string; detail: string | null } {
  const rawGrams = i.whole_meal_grams ?? i.grams ?? i.display_grams
  const g = numeric(rawGrams)
  const n = numeric(i.display_amount)
  let unit = i.display_unit
  let value = n
  if (unit === 'L' && n !== null) { unit = 'mL'; value = n * 1000 }
  if (value !== null && unit) {
    const separator = ['g', 'mL', 'kg'].includes(unit) ? ' ' : ''
    const label = Number.isInteger(value) ? `${value}${separator}${unit}`
      : value < 1 ? `少於 1${separator}${unit}`
      : `${Math.floor(value)}–${Math.ceil(value)}${separator}${unit}`
    const detail = `${i.display_amount} ${i.display_unit}${rawGrams != null ? `；本餐計算量：${rawGrams} g` : ''}`
    return { label, detail: !Number.isInteger(value) || rawGrams != null ? detail : null }
  }
  if (i.display_text) {
    // Older saved meals sometimes store numeric volume only in display_text.
    const legacy = i.display_text.match(/^(約\s*)?(\d+(?:\.\d+)?(?:\/\d+)?)\s*(L|mL|g|大匙|小匙)$/)
    if (legacy) {
      const converted = cookingQuantity({ ...i, display_text: null, display_amount: legacy[2], display_unit: legacy[3] })
      return { label: `${legacy[1] ? '約 ' : ''}${converted.label}`,
        detail: `原顯示用量：${i.display_text}${rawGrams != null ? `；本餐計算量：${rawGrams} g` : ''}` }
    }
    return { label: i.display_text, detail: rawGrams != null ? `本餐計算量：${rawGrams} g` : null }
  }
  if (g === null) return { label: '用量未知', detail: null }
  return { label: Number.isInteger(g) ? `${g} g` : g < 1 ? '少於 1 g' : `約 ${Math.round(g)} g`,
    detail: Number.isInteger(g) ? null : `本餐計算量：${rawGrams} g` }
}
