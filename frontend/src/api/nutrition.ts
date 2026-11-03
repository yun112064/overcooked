/** 營養顯示共用的常數與換算（畫面用；計算與判定由後端以精確值完成）。 */

/** 七種營養素（顯示順序與單位） */
export const NUTRIENTS = [
  { key: 'energy_kcal', label: '熱量', unit: 'kcal' },
  { key: 'protein_g', label: '蛋白質', unit: 'g' },
  { key: 'carbohydrate_g', label: '碳水化合物', unit: 'g' },
  { key: 'fat_g', label: '脂肪', unit: 'g' },
  { key: 'sugars_g', label: '糖', unit: 'g' },
  { key: 'sodium_mg', label: '鈉', unit: 'mg' },
  { key: 'dietary_fiber_g', label: '膳食纖維', unit: 'g' },
] as const

/** 精確字串（含 a/b 分數）轉成數字；null 表示未知。 */
export function toNumber(value: string | null | undefined): number | null {
  if (value == null) return null
  const [a, b] = value.split('/')
  const n = b === undefined ? Number(a) : Number(a) / Number(b)
  return Number.isFinite(n) ? n : null
}

/** 飯顯示總克數並附碗數：160 克為一碗，取到 0.1 碗（例：304 g（約 1.9 碗））。 */
export const GRAMS_PER_BOWL = 160
export function rice(grams: string | null | undefined) {
  const g = toNumber(grams)
  if (g === null) return '用量未知'
  const b = Math.round((g / GRAMS_PER_BOWL) * 10) / 10
  return `${Math.round(g)} g（約 ${b === 0 ? '不到 0.1' : String(b)} 碗）`
}

/** 食譜裡的飯：顯示總克數並附碗數 */
const RICE_NAMES = new Set(['白飯', '糙米飯', '飯', '米飯', '五穀飯', '紫米飯'])

const SPOON_FRACTIONS: Record<number, string> = { 0.25: '¼', 0.5: '½', 0.75: '¾' }
/** 量匙用量到 ¼（1.5 → 1½、0.25 → ¼）。 */
function spoonAmount(value: string) {
  const n = Number(value)
  const whole = Math.floor(n)
  const frac = SPOON_FRACTIONS[Math.round((n - whole) * 100) / 100]
  if (!frac) return String(n)
  return whole > 0 ? `${whole}${frac}` : frac
}

/** 畫面用量：調味料顯示小匙／大匙（或「少許」），飯顯示克數與碗數，其他顯示克數（10 克以下到 0.1）。 */
export function ingredientAmount(i: {
  name: string; grams: string | null; display_amount: string | null; display_unit: string | null; display_text: string | null
}) {
  if (i.display_text) return i.display_text
  if (i.display_amount != null && i.display_unit) return `${spoonAmount(i.display_amount)} ${i.display_unit}`
  if (RICE_NAMES.has(i.name)) return rice(i.grams)
  const g = toNumber(i.grams)
  return g === null ? '用量未知' : `${g < 10 ? g.toFixed(1) : g.toFixed(0)} g`
}
