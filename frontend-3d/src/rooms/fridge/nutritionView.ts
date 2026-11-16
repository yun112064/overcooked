/**
 * 餐單明細裡營養與食材的顯示換算（搬自舊前端的 PersonNutrition、ResultPersonNutrition、DishDetail）。
 * 兩種紀錄（資料庫菜單的快照、生成結果的快照）的每人營養欄位不同，這裡先整理成同一個形狀再畫。
 * 只做顯示用的取整；計算與判定都是後端的事。
 */
import { rice, toNumber } from '../../api/nutrition'
import type { DailyReference, MealPlanDetail, MealResult, MealType, ResultIngredient } from '../../api/types'

/** 顯示用取整：kcal、mg 到整數，其餘到 0.1 */
export function fmt(value: number, unit: string): string {
  return unit === 'kcal' || unit === 'mg' ? `${Math.round(value)}` : value.toFixed(1)
}

export type ReferenceView = { text: string; base: number; low: number | null; high: number | null }

/** 每日建議的文字與比較基準（範圍用上下限、上限用上限、建議值用建議值）。 */
export function reference(d: DailyReference | undefined, unit: string): ReferenceView | null {
  if (!d) return null
  const min = toNumber(d.min)
  const max = toNumber(d.max)
  const value = toNumber(d.value)
  if (d.kind === 'range' && min !== null && max !== null) {
    return { text: `${Math.round(min)}–${Math.round(max)} ${unit}`, base: max, low: min, high: max }
  }
  if (d.kind === 'max' && max !== null) return { text: `< ${Math.round(max)} ${unit}`, base: max, low: null, high: max }
  if (value !== null) return { text: `${fmt(value, unit)} ${unit}`, base: value, low: value, high: value }
  return null
}

/** 攝取量佔每日建議的百分比；範圍類顯示兩端。基準不是正數就不算（不顯示 Infinity%）。 */
export function percentText(intake: number, ref: ReferenceView): string | null {
  const pct = (base: number) => Math.round((intake / base) * 100)
  if (ref.low !== null && ref.high !== null && ref.low !== ref.high) {
    return ref.low > 0 && ref.high > 0 ? `${pct(ref.high)}–${pct(ref.low)}%` : null
  }
  return ref.base > 0 ? `${pct(ref.base)}%` : null
}

/** 占比長條的狀態：上限類（鈉、糖）超過這一餐的份額要提醒，超過整天上限更嚴重。 */
export function barTone(ratio: number, mealRatio: number, kind: DailyReference['kind']): 'normal' | 'overMeal' | 'overDay' {
  if (kind !== 'max') return 'normal'
  if (ratio > 1) return 'overDay'
  return ratio > mealRatio ? 'overMeal' : 'normal'
}

// ---- 每人營養：兩種紀錄整理成同一個形狀 ----

export type PortionView = {
  /** 食量 n（顯示用） */
  n: number | null
  /** 這一份應占每日的比例（例如晚餐 0.4 × 食量） */
  mealRatio: number | null
  intake: Record<string, string | null>
  knownPartial: Record<string, string | null | undefined>
  /** 資料庫菜單才有：這一份分到的菜與主食（已組成文字） */
  portionText?: string
}

export type PersonView = {
  id: string
  name: string
  kinds: ('dining' | 'bento')[]
  /** 在家吃的那一份；只帶便當為 null */
  dining: PortionView | null
  bento: (PortionView & { meal: MealType }) | null
  daily: Record<string, DailyReference>
}

type Snapshot = MealPlanDetail['snapshot']
type SnapshotPortion = NonNullable<NonNullable<Snapshot['people']>[number]['dining']>

/** 這一份分到的量：每道菜的份數（整份＝1，全家加總為 1）與主食克數／碗數。 */
function portionText(portion: SnapshotPortion, dishName: Map<string, string>, staple: string | null): string {
  const share = toNumber(portion.dish_share)
  const dishes = portion.dish_ids.map((id) => `${dishName.get(id) ?? id} ${share === null ? '份量未知' : `${share.toFixed(2)} 份`}`)
  if (portion.staple_g) dishes.push(`${staple ?? '主食'} ${rice(portion.staple_g)}`)
  return dishes.join('、')
}

/** 資料庫菜單的快照（snapshot.people）。 */
export function peopleFromSnapshot(snapshot: Snapshot): PersonView[] {
  const dishName = new Map((snapshot.selected ?? []).map((r) => [r.recipe_id, r.recipe_name]))
  const staple = snapshot.staple_choice ?? null
  const view = (p: SnapshotPortion): PortionView => ({
    n: toNumber(p.n), mealRatio: toNumber(p.meal_ratio), intake: p.intake, knownPartial: p.known_partial,
    portionText: portionText(p, dishName, staple),
  })
  return (snapshot.people ?? []).map((p) => ({
    id: p.member_id,
    name: p.name ?? '成員',
    kinds: p.kinds,
    dining: p.dining ? view(p.dining) : null,
    bento: p.bento ? { ...view(p.bento), meal: p.bento.meal } : null,
    daily: p.daily,
  }))
}

/** 生成結果的快照（nutrition.per_person）。舊結果沒有 dining 欄：用餐與便當當時合併計算，照舊當成在家吃。 */
export function peopleFromResult(nutrition: MealResult['nutrition']): PersonView[] {
  return (nutrition.per_person ?? []).map((p) => ({
    id: p.member_id,
    name: p.name ?? '已刪除成員',
    kinds: p.kinds,
    dining: (p.dining ?? true)
      ? { n: toNumber(p.n), mealRatio: toNumber(p.meal_ratio), intake: p.intake, knownPartial: p.known_partial }
      : null,
    bento: p.bento
      ? { n: toNumber(p.bento.n), mealRatio: toNumber(p.bento.meal_ratio), intake: p.bento.intake, knownPartial: p.bento.known_partial, meal: p.bento.meal }
      : null,
    daily: p.daily,
  }))
}

/** 這一份的熱量占每日建議的比例；任一邊未知為 null。 */
export function energyShare(portion: PortionView, daily: Record<string, DailyReference>): number | null {
  const energy = toNumber(portion.intake.energy_kcal)
  const perDay = toNumber(daily.energy_kcal?.value)
  return energy === null || !perDay ? null : energy / perDay
}

// ---- 三大營養素的熱量比例 ----

/** 參考範圍（國健署：碳水 50–60%、脂肪 20–30%、蛋白質 10–20%）。 */
const MACROS = [
  { key: 'carbohydrate_g', label: '碳水', kcal: 4, range: [50, 60] },
  { key: 'protein_g', label: '蛋白質', kcal: 4, range: [10, 20] },
  { key: 'fat_g', label: '脂肪', kcal: 9, range: [20, 30] },
] as const

export type MacroRow = { key: string; label: string; grams: number; pct: number; range: readonly [number, number]; verdict: '' | '偏低' | '偏高' }

/** 三項都已知才算比例；有任何一項未知就回傳 null（不畫一條缺一塊的長條）。 */
export function macroSplit(nutrition: Pick<MealResult['nutrition'], 'protein_g' | 'other_nutrients'>): MacroRow[] | null {
  const grams = MACROS.map((m) => toNumber(m.key === 'protein_g' ? nutrition.protein_g : nutrition.other_nutrients?.[m.key]))
  if (grams.some((g) => g === null)) return null
  const total = MACROS.reduce((sum, m, i) => sum + (grams[i] as number) * m.kcal, 0)
  if (!total) return null
  return MACROS.map((m, i) => {
    const g = grams[i] as number
    const pct = (g * m.kcal / total) * 100
    return { key: m.key, label: m.label, grams: g, pct, range: m.range, verdict: pct < m.range[0] ? '偏低' : pct > m.range[1] ? '偏高' : '' }
  })
}

// ---- 生成結果的食材與步驟 ----

type ResultIngredientView = Pick<ResultIngredient, 'name' | 'group' | 'display_amount' | 'display_unit' | 'display_text' | 'display_grams'>

const FRACTIONS: Record<number, string> = { 0.25: '¼', 0.5: '½', 0.75: '¾' }

/** 量匙用量取到 ¼（1.5 → 1½、0.25 → ¼）；其他數量原樣 */
function spoon(value: string): string {
  const n = Number(value)
  const whole = Math.floor(n)
  const frac = FRACTIONS[Math.round((n - whole) * 100) / 100]
  if (!frac) return String(n)
  return whole > 0 ? `${whole}${frac}` : frac
}

/** 後端已取整的畫面用量；沒有數量時顯示原文（如「少許」），都沒有就老實說無法換算。 */
export function resultAmount(i: ResultIngredientView): string {
  if (i.display_amount != null && i.display_unit === 'L') return `${Number(i.display_amount)} L`
  if (i.display_amount != null && i.display_unit) return `${spoon(i.display_amount)} ${i.display_unit}`
  if (i.display_text) return i.display_text
  const grams = toNumber(i.display_grams)
  return grams === null ? '無法換算' : `${Math.round(grams)} g`
}

/** 沒有分組的先列，其後依食譜原文的分組（如「調味」「醃料」）。 */
export function groupIngredients<T extends { group?: string | null }>(ingredients: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>()
  for (const i of ingredients) {
    const key = i.group ?? ''
    groups.set(key, [...(groups.get(key) ?? []), i])
  }
  return [...groups.entries()].sort(([a], [b]) => (a === '' ? (b === '' ? 0 : -1) : b === '' ? 1 : 0))
}

/** 顯示分組代稱已展開的步驟（steps_display）；舊資料沒有、或筆數對不上時用原文。 */
export function stepLines(steps: string[] | undefined, display: string[] | undefined): string[] {
  const original = steps ?? []
  return display && display.length === original.length ? display : original
}

// ---- 生成結果的主食 ----

type StapleView = Pick<NonNullable<MealResult['staple']>, 'label' | 'tfda_name' | 'display_amount' | 'display_unit'>

/** 主食沒有營養資料的原因代碼（和餐桌那一景同一組） */
const STAPLE_NO_DATA = new Set(['no_tfda_cooked_sample', 'staple_without_tfda_sample'])

/** 主食的整餐份量，寫法和餐桌相同（TFDA 食材名＋數量＋單位）；算不出來回 null，不寫成 0。 */
export function stapleAmount(staple: StapleView): string | null {
  // 空字串不是 0：toNumber('') 會得到 0，要先擋掉
  const amount = staple.display_amount?.trim() ? toNumber(staple.display_amount) : null
  if (amount === null) return null
  return `${staple.tfda_name ?? staple.label} ${amount} ${staple.display_unit ?? ''}`.trim()
}

/** 主食份量算不出來的原因。紀錄是唯讀的快照，所以只說明原因，不叫人重新生成。 */
export function stapleProblem(reason: string | null | undefined): string {
  if (reason === 'staple_not_supported') return '這個主食不在可選清單中，份量沒有算出來'
  if (reason && STAPLE_NO_DATA.has(reason)) return '這個主食當時沒有營養資料，份量沒有算出來'
  return '無法換算成克數'
}

/** 每日鈉上限（mg），與後端 config/daily_reference_nutrients.json 一致；只用於提示文字 */
export const SODIUM_DAILY_MAX = 2400

/** 高鈉提醒的說明；不是高鈉、或鈉未知時回傳 null（不提醒，也不猜）。 */
export function sodiumNote(high: boolean | null | undefined, sodium: string | null | undefined): { label: string; detail: string } | null {
  const mg = toNumber(sodium)
  if (!high || mg === null) return null
  return {
    label: `高鈉・${Math.round(mg)} mg`,
    detail: `每人份約 ${Math.round(mg)} mg 鈉，約占每日上限 ${SODIUM_DAILY_MAX} mg 的 ${Math.round((mg / SODIUM_DAILY_MAX) * 100)}%`,
  }
}

/** 來源連結只接受一般網址；其他（空的、javascript: 之類）回傳 null，改成只顯示文字。 */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    return ['http:', 'https:'].includes(new URL(url).protocol) ? url : null
  } catch {
    return null
  }
}
