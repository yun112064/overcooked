/**
 * 結果頁的文字與換算（從舊前端的 MealResultView、DishDetail、ResultPersonNutrition 搬過來，規則不變）。
 * 都是純函式：顯示用的取整在這裡做，計算與判定一律由後端以精確值完成。
 * null／undefined 代表未知，這裡絕不把它變成 0。
 */
import { toNumber } from '../../api/nutrition'
import { amount, type Nutrition } from '../../api/resultNutrition'
import type {
  DailyReference, DecisionKind, MealResult, NutritionSuggestion, ResultIngredient, RetryState,
} from '../../api/types'

/** 重試工作還在跑的狀態 */
export const ACTIVE = new Set(['queued', 'running'])

/** 網址上的版本：只接受正整數，其餘（空的、亂打的）回 null。 */
export function parseVersion(param: string | undefined): number | null {
  if (!param || !/^[1-9]\d*$/.test(param)) return null
  const n = Number(param)
  return Number.isSafeInteger(n) ? n : null
}

// ---- 用量 ----

/** 克數取到 1 g；未知寫「無法換算」。 */
export function grams(v: string | null | undefined): string {
  const value = toNumber(v)
  return value === null ? '無法換算' : `${Math.round(value)} g`
}

const milliliters = new Intl.NumberFormat('zh-TW', { maximumSignificantDigits: 6 })
export function volume(value: string): string {
  const ml = toNumber(value)
  return ml === null ? '無法換算' : `${milliliters.format(ml)} mL`
}

const FRACTIONS: Record<number, string> = { 0.25: '¼', 0.5: '½', 0.75: '¾' }

/** 調味料的量匙用量取到 ¼（1.5 → 1½、0.25 → ¼）；其他數量原樣。 */
function spoons(value: string): string {
  const n = Number(value)
  const whole = Math.floor(n)
  const frac = FRACTIONS[Math.round((n - whole) * 100) / 100]
  if (!frac) return String(n)
  return whole > 0 ? `${whole}${frac}` : frac
}

type ShownIngredient = Pick<ResultIngredient, 'name' | 'group' | 'display_amount' | 'display_unit' | 'display_text' | 'display_grams'>

/** 後端已取整的畫面用量；沒有數量時顯示原文（如「少許」），都沒有才是「無法換算」。 */
export function ingredientAmount(i: ShownIngredient): string {
  if (i.display_amount != null && i.display_unit === 'L') return `${Number(i.display_amount)} L`
  if (i.display_amount != null && i.display_unit) return `${spoons(i.display_amount)} ${i.display_unit}`
  if (i.display_text) return i.display_text
  return i.display_grams == null ? '無法換算' : `${Math.round(Number(i.display_grams))} g`
}

/** 食材分組：沒有分組的先列，其後依食譜原文的分組（如「調味」「醃料」）。 */
export function groupIngredients<T extends { group?: string | null }>(ingredients: readonly T[]): { group: string; items: T[] }[] {
  const groups = new Map<string, T[]>()
  for (const i of ingredients) {
    const key = i.group ?? ''
    groups.set(key, [...(groups.get(key) ?? []), i])
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === '' ? -1 : b === '' ? 1 : 0))
    .map(([group, items]) => ({ group, items }))
}

/** 步驟：顯示分組代稱已展開的文字（steps_display）；舊資料沒有時用原文。 */
export function stepLines(steps: readonly string[], display?: readonly string[]): readonly string[] {
  return display && display.length === steps.length ? display : steps
}

// ---- 高鈉 ----

/** 每日鈉上限（mg），與 config/daily_reference_nutrients.json 一致；只用於提示文字 */
const SODIUM_DAILY_MAX = 2400

/** 高鈉提醒（只提醒，不影響採用）。不是高鈉或沒有數值時回 null。 */
export function highSodium(high: boolean | null | undefined, sodium: string | null | undefined): { tag: string; detail: string } | null {
  if (!high || sodium == null) return null
  const mg = Number(sodium)
  return {
    tag: `高鈉・${Math.round(mg)} mg`,
    detail: `每人份約 ${Math.round(mg)} mg 鈉，約占每日上限 ${SODIUM_DAILY_MAX} mg 的 ${Math.round((mg / SODIUM_DAILY_MAX) * 100)}%`,
  }
}

// ---- 主食 ----

/** 主食克數未知的原因 → 給使用者看的說明；不是份量問題，而是主食沒有營養資料或不在清單。 */
const STAPLE_NO_DATA = new Set(['no_tfda_cooked_sample', 'staple_without_tfda_sample'])
const STAPLE_REASONS = new Set([...STAPLE_NO_DATA, 'staple_not_supported'])

export function stapleProblem(label: string, reason: string | null | undefined): string {
  if (reason === 'staple_not_supported') return `主食「${label}」不在可選清單中，整餐營養無法計算；請改選清單中的主食後重新生成`
  if (reason && STAPLE_NO_DATA.has(reason)) return `主食「${label}」目前沒有營養資料，整餐營養無法計算；請改選其他主食後重新生成`
  return `主食「${label}」無法換算克數，整餐營養無法計算`
}

/** 主食的份量；算不出來時回 null（畫面改寫原因）。 */
export function stapleAmount(staple: NonNullable<MealResult['staple']>): string | null {
  if (staple.display_amount == null) return null
  return `${staple.tfda_name ?? staple.label} ${Number(staple.display_amount)} ${staple.display_unit ?? ''}`.trim()
}

// ---- 價格 ----

/** 查價失敗原因 → 使用者看得懂的說明；原始原因放在 title，方便追查。 */
const MISSING_PRICE_REASONS: Record<string, string> = {
  NO_PRICE_DATA: '尚無價格資料',
  UNIT_MISMATCH: '用量與包裝單位不相符',
  PRICE_EXPIRED: '價格已過期',
  PACKAGE_DEMAND_UNITS_REQUIRE_RECONCILIATION: '同商品含重量與容量需求，整包採購量待確認',
  CURRENCY_MISMATCH: '報價幣別不適用',
  PRICE_NOT_YET_VALID: '報價尚未生效',
  PRICE_REVALIDATION_REQUIRED: '僅有歷史價格，需重新查價',
  INVALID_PACKAGE: '包裝資訊不完整',
}

export function missingReason(code: string | null): string {
  return (code && MISSING_PRICE_REASONS[code]) || '賣場找不到相符的商品'
}

export function shortTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-TW', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })
}

/**
 * 收據上的印章：預算內／超出預算；價格不完整或沒有預算時不蓋章（回 null）。
 * - 沒有預算（食譜庫選菜不問預算）時後端的 budget_status 仍然是 OK，所以要自己看 budget，不能只看狀態。
 * - 後端比的是「這餐吃掉」的按比例成本，整包花費不參與；full 把比的是哪一個金額寫出來，
 *   免得「預算內」被讀成結帳要付的整包也在預算內。
 */
export function budgetStamp(prices: MealResult['prices']): { tone: 'ok' | 'over'; text: string; full: string } | null {
  if (prices.status !== 'computed' || prices.budget === null) return null
  if (prices.budget_status === 'OK') return { tone: 'ok', text: '預算內', full: '吃掉的在預算內' }
  if (prices.budget_status === 'BUDGET_EXCEEDED') return { tone: 'over', text: '超出預算', full: '吃掉的超出預算' }
  return null
}

// ---- 決定、避免重複 ----

export const DECISION_LABELS: Record<DecisionKind, string> = {
  accept_missing_price: '接受缺價例外並繼續',
  continue_missing_nutrition: '繼續查看（仍不可採用）',
}

export const EXCLUDED_FROM: Record<string, string> = {
  previous_day: '昨天', breakfast: '今天早餐', lunch: '今天午餐', dinner: '今天晚餐',
}

/** 「避免重複的菜」那一段說明。 */
export function previousDayText(previous: MealResult['previous_day']): string {
  // 舊結果可能沒有記下昨天的日期：就不寫括號，不把 null 印出來
  const yesterday = previous.previous_date ? `昨天（${previous.previous_date}）` : '昨天'
  if (!previous.has_records) return `${yesterday}與今天其他餐都沒有已採用紀錄，未排除任何菜。`
  const excluded = previous.excluded_dishes.length > 0
    ? `已排除：${previous.excluded_dishes.map((d) => `${d.dish_name}（${EXCLUDED_FROM[d.from ?? 'previous_day'] ?? '昨天'}）`).join('、')}。`
    : '沒有需要排除的菜。'
  return `已比對${yesterday}與今天其他餐的已採用紀錄；${excluded}`
}

// ---- 調整建議 ----

const NUTRIENT_LABELS: Record<string, string> = { energy_kcal: '熱量', protein_g: '蛋白質' }
const NUTRIENT_UNITS: Record<string, string> = { energy_kcal: 'kcal', protein_g: 'g' }

/** 調整建議的差額與可調範圍由後端以每 1 份（1n）基準計算；乘上份數 N 換成整餐值，與整餐營養表一致（決策 #53）。 */
function scale(v: string | null | undefined, n: number): string | null | undefined {
  return v === null || v === undefined ? v : String(Number(v) * n)
}

const isStapleReason = (reason: string | null | undefined) => Boolean(reason && STAPLE_REASONS.has(reason))

/** 一則調整建議的文字（整餐值）。 */
export function suggestionText(s: NutritionSuggestion, totalN: number): string {
  if (s.code === 'NUTRITION_OUT_OF_RANGE' && s.nutrient) {
    const unit = NUTRIENT_UNITS[s.nutrient]
    const head = `${NUTRIENT_LABELS[s.nutrient]}需${s.direction === 'increase' ? '增加' : '減少'}約 ${amount(scale(s.needed_change, totalN), unit)}；`
    const body = s.ingredients.length
      ? s.ingredients.map((i) => `${i.name}${i.direction === 'increase' ? '增加' : '減少'}`
        + (i.adjustment_approved
          ? `（可調 ${grams(scale(i.adjustable_min, totalN))}–${grams(scale(i.adjustable_max, totalN))}，最多${amount(scale(i.max_change, totalN), unit)}）`
          : '（未審核可調範圍）')).join('、')
      : '沒有可調整的食材'
    return head + body + (s.within_adjustable_ranges ? '。已審核範圍內補得足，但與另一項營養無法同時達成。' : '。已審核範圍內補不足。')
  }
  const others = s.ingredients.filter((i) => !isStapleReason(i.reason))
  return [
    ...s.ingredients.filter((i) => isStapleReason(i.reason)).map((i) => stapleProblem(i.name, i.reason)),
    ...(others.length ? [`${s.action ?? ''}：${others.map((i) => i.name).join('、')}`] : []),
  ].join('；')
}

// ---- 每人營養 ----

/** 顯示用取整：kcal、mg 到整數，其餘到 0.1 */
export function fmt(v: number, unit: string): string {
  return unit === 'kcal' || unit === 'mg' ? `${Math.round(v)}` : v.toFixed(1)
}

export type DailyRef = { text: string; base: number; low: number | null; high: number | null }

/** 每日建議的文字與比較基準（範圍用上下限、上限用上限、建議值用建議值）。 */
export function dailyRef(d: DailyReference | undefined, unit: string): DailyRef | null {
  if (!d) return null
  const min = d.min !== undefined ? Number(d.min) : null
  const max = d.max !== undefined ? Number(d.max) : null
  const value = d.value !== undefined ? Number(d.value) : null
  if (d.kind === 'range' && min !== null && max !== null) {
    return { text: `${Math.round(min)}–${Math.round(max)} ${unit}`, base: max, low: min, high: max }
  }
  if (d.kind === 'max' && max !== null) return { text: `< ${Math.round(max)} ${unit}`, base: max, low: null, high: max }
  if (value !== null) return { text: `${fmt(value, unit)} ${unit}`, base: value, low: value, high: value }
  return null
}

/** 這一餐吃到的量占每日建議的百分比；建議是範圍時寫成兩個百分比。 */
export function dailyPercent(intake: number, ref: DailyRef): string {
  if (ref.low !== null && ref.high !== null && ref.low !== ref.high) {
    return `${Math.round((intake / ref.high) * 100)}–${Math.round((intake / ref.low) * 100)}%`
  }
  return `${Math.round((intake / ref.base) * 100)}%`
}

/**
 * 長條的顏色：上限類（糖、鈉）超過每日上限是 over、超過這一餐的份額是 high，其餘 normal。
 * 回傳的 note 是配合顏色的文字，不只靠顏色分辨。
 */
export function barTone(kind: DailyReference['kind'], ratio: number, mealRatio: number): { tone: 'normal' | 'high' | 'over'; note: string | null } {
  if (kind === 'max' && ratio > 1) return { tone: 'over', note: '超過每日上限' }
  if (kind === 'max' && ratio > mealRatio) return { tone: 'high', note: '超過這一餐的份額' }
  return { tone: 'normal', note: null }
}

/** 三大營養素占熱量比例的參考範圍（國健署：碳水 50–60%、脂肪 20–30%、蛋白質 10–20%）。 */
const MACROS = [
  { key: 'carbohydrate_g', label: '碳水', kcal: 4, range: [50, 60] as const, tone: 'carb' as const },
  { key: 'protein_g', label: '蛋白質', kcal: 4, range: [10, 20] as const, tone: 'protein' as const },
  { key: 'fat_g', label: '脂肪', kcal: 9, range: [20, 30] as const, tone: 'fat' as const },
]

export type MacroRow = {
  key: string; label: string; tone: 'carb' | 'protein' | 'fat'
  grams: number; percent: number; range: readonly [number, number]
  /** 在建議範圍內是 null；否則「偏低」或「偏高」 */
  off: '偏低' | '偏高' | null
}

/** 整餐三大營養素的熱量比例；任何一項未知就整個不算（回 null），不拿 0 代替。 */
export function macroRows(nutrition: Nutrition): MacroRow[] | null {
  const parts = MACROS.map((m) => {
    const v = m.key === 'protein_g' ? nutrition.protein_g : nutrition.other_nutrients?.[m.key]
    return { ...m, g: v === null || v === undefined ? null : Number(v) }
  })
  if (parts.some((p) => p.g === null || !Number.isFinite(p.g))) return null
  const total = parts.reduce((sum, p) => sum + (p.g as number) * p.kcal, 0)
  if (!total) return null
  return parts.map((p) => {
    const percent = (((p.g as number) * p.kcal) / total) * 100
    return {
      key: p.key, label: p.label, tone: p.tone, grams: p.g as number, percent, range: p.range,
      off: percent < p.range[0] ? '偏低' : percent > p.range[1] ? '偏高' : null,
    }
  })
}

// ---- 採用 ----

/**
 * 採用這一版要送給 POST /meal-plans 的內容（和舊前端相同）。
 * 前端只指定版本；資格、版本、缺價接受與昨日歷史由後端在交易內重驗。
 * 後端說「缺價例外已接受」時才附上那一筆決定的 id；沒有那筆決定（2026-10-26 之後缺價不必先決定）就不附。
 */
export function adoptBody(requestId: string, r: Pick<MealResult, 'result_version' | 'request_version' | 'eligibility'>) {
  const accepted = r.eligibility.price_exception_accepted
    ? r.eligibility.decisions.find((d) => d.kind === 'accept_missing_price')?.id
    : undefined
  return {
    meal_request_id: requestId, result_version: r.result_version, request_version: r.request_version,
    ...(accepted ? { price_exception_decision_id: accepted } : {}),
  }
}

// ---- 重試 ----

type Retries = { price: RetryState | null; knowledge: RetryState | null } | undefined

export function retryActive(state: RetryState | null | undefined): boolean {
  return !!state && ACTIVE.has(state.status)
}

/**
 * 重試（查價／小知識）這個命令的名字，用來決定冪等鍵：同一個名字固定一把鍵。
 * 同一次嘗試名字不變（重複點擊、網路重送只成立一次）；上一次重試已經失敗時換一個名字——
 * 後端收到同一把鍵只會回同一個失敗的工作，畫面寫的「稍後再試」就永遠沒有作用
 * （和舊前端 GenerationPanel「失敗後重新送出要換一把鍵」是同一條規則）。
 */
export function retryCommandName(resultVersion: number, command: 'retry' | 'knowledge-retry', last: RetryState | null | undefined): string {
  const name = `${resultVersion}:${command}`
  return last?.status === 'failed' ? `${name}:${last.job_id}` : name
}

/** 重試成功後產生的新版本（和目前看的不同才算）；兩種重試都產生了就取比較新的。 */
export function producedVersion(retries: Retries, current: number | null): number | null {
  const versions = [retries?.price, retries?.knowledge]
    .filter((s): s is RetryState => !!s && s.status === 'succeeded' && !!s.produced_result_version && s.produced_result_version !== current)
    .map((s) => s.produced_result_version as number)
  return versions.length ? Math.max(...versions) : null
}

/** YYYY-MM-DD → 11/14；不是日期就原樣回傳。 */
export function monthDay(date: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(date)
  return match ? `${Number(match[2])}/${Number(match[3])}` : date
}
