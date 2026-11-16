/**
 * 冰箱門（紀錄）用到的純邏輯：日期與月曆、趨勢逐日加總與統計、已採用餐單的判斷。
 * 規則都搬自舊前端的 MealHistoryPage／GenerationHistoryPage／MarketCheckoutButton，
 * 差別只有一個：未知（null 或沒有這個欄位）一律當未知，不會被加成 0 或變成 NaN。
 */
import { toNumber } from '../../api/nutrition'
import type { GenerationPlanDetail, MealPlanDetail, MealRequestSummary, MealResult, MealType } from '../../api/types'
import { MEAL_LABELS } from '../../api/types'

export type Tab = 'adopted' | 'nutrition'
export const TREND_DAYS = [7, 30, 90] as const
export type TrendDays = (typeof TREND_DAYS)[number]
export type TrendKind = 'cost' | 'energy'

type Numeric = string | number | null | undefined

/** 後端的數值有三種樣子：精確字串（含 a/b 分數）、數字、null。未知回傳 null，不當成 0。 */
export function num(value: Numeric): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value !== 'string' || value.trim() === '') return null
  return toNumber(value)
}

// ---- 日期（一律用使用者本機的日曆日，不經過 UTC） ----

const pad = (n: number) => String(n).padStart(2, '0')
const WEEKDAYS = '日一二三四五六'

export function isoDay(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 'YYYY-MM-DD' → 本機午夜的 Date；格式不對或日期不存在（2 月 30 日）回傳 null。 */
export function parseDay(iso: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) return null
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  return isoDay(d) === iso ? d : null
}

/** 網址上的 date 參數：不是有效日期就當作沒給。 */
export function validDay(value: string | null | undefined): string | null {
  return value && parseDay(value) ? value : null
}

export function addDays(iso: string, n: number): string {
  const d = parseDay(iso)
  if (!d) return iso
  d.setDate(d.getDate() + n)
  return isoDay(d)
}

/** 2026-11-04 → 11/4 */
export function dayLabel(iso: string): string {
  const d = parseDay(iso)
  return d ? `${d.getMonth() + 1}/${d.getDate()}` : iso
}

/** 2026-11-14 → 週六 */
export function weekdayLabel(iso: string): string {
  const d = parseDay(iso)
  return d ? `週${WEEKDAYS[d.getDay()]}` : ''
}

/** 便利貼在畫面上的元素 id：物件列點到它時，把鍵盤焦點帶過去。 */
export function noteElementId(id: string): string {
  return `fridge-note-${id}`
}

/** 給讀屏與標題用：11 月 14 日（週六） */
export function longDayLabel(iso: string): string {
  const d = parseDay(iso)
  return d ? `${d.getMonth() + 1} 月 ${d.getDate()} 日（${weekdayLabel(iso)}）` : iso
}

// ---- 月曆 ----

/** 2026-11-14 → 2026-11 */
export function monthOf(iso: string): string {
  return iso.slice(0, 7)
}

export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`
}

export function monthTitle(month: string): string {
  const [y, m] = month.split('-').map(Number)
  return `${y} 年 ${m} 月`
}

/** 這個月的格子，週日開頭；前後補 null 湊滿整週（只畫需要的週數，月曆才不會多一排空白）。 */
export function monthCells(month: string): (string | null)[] {
  const [y, m] = month.split('-').map(Number)
  const lead = new Date(y, m - 1, 1).getDay()
  const count = new Date(y, m, 0).getDate()
  const cells: (string | null)[] = Array.from({ length: lead }, () => null)
  for (let day = 1; day <= count; day++) cells.push(`${month}-${pad(day)}`)
  while (cells.length % 7 !== 0) cells.push(null)
  return cells
}

/** 月曆要顯示哪個月：日期變了（點長條、上一頁）就跟著日期走；自己翻月份時才用翻到的那個月。 */
export function shownMonth(date: string, turned: { forDate: string; month: string } | null): string {
  return turned && turned.forDate === date && parseDay(`${turned.month}-01`) ? turned.month : monthOf(date)
}

export type DateCount = { meal_date: string; count: number }

/**
 * 後端的日期清單只回最近 60 個有紀錄的日子。看更早的月份時貼紙可能不完整，
 * 要告訴使用者「沒有貼紙不代表沒有紀錄」。
 */
export const DATE_LIST_LIMIT = 60
export function marksMayBeIncomplete(dates: DateCount[], month: string): boolean {
  if (dates.length < DATE_LIST_LIMIT) return false
  const earliest = dates.reduce((min, d) => (d.meal_date < min ? d.meal_date : min), dates[0].meal_date)
  return month <= monthOf(earliest)
}

// ---- 趨勢 ----

export type TrendItem = {
  meal_date: string
  meal_type: string
  energy_kcal: Numeric
  protein_g: Numeric
  target_energy_kcal: Numeric
  target_protein_g: Numeric
  /** 按比例成本；未計算的紀錄為 null（未知，不是 0） */
  cost_total?: Numeric
  known_cost_total?: Numeric
  budget?: Numeric
}

export type Trend = { since: string; until: string; items: TrendItem[] }

/**
 * 舊前端有兩個地方用同一個 query key 存不同形狀（整包 {since, until, items} 或只有 items）。
 * 這裡兩種都接，免得別的房間先放進快取時這裡讀不到 since。
 */
export function normalizeTrend(raw: unknown, days: number, today: string): Trend {
  if (Array.isArray(raw)) return { since: addDays(today, -(days - 1)), until: today, items: raw as TrendItem[] }
  const body = (raw ?? {}) as Partial<Trend>
  const until = validDay(body.until) ?? today
  return {
    since: validDay(body.since) ?? addDays(until, -(days - 1)),
    until,
    items: Array.isArray(body.items) ? body.items : [],
  }
}

export type DayPoint = {
  date: string
  label: string
  meals: number
  energy: number | null
  target: number | null
  cost: number | null
  budget: number | null
  /** 這天有任何一餐的熱量／成本未知；數值只算已知的部分 */
  unknownEnergy: boolean
  unknownCost: boolean
}

/** 逐日加總；沒有紀錄的日子留空，讓時間軸連續。價格不完整時以已知部分計，並標記 unknownCost。 */
export function byDay(items: TrendItem[], since: string, days: number): DayPoint[] {
  const points = new Map<string, DayPoint>()
  for (let i = 0; i < days; i++) {
    const date = addDays(since, i)
    points.set(date, {
      date, label: dayLabel(date), meals: 0, energy: null, target: null, cost: null, budget: null,
      unknownEnergy: false, unknownCost: false,
    })
  }
  const add = (sum: number | null, value: number | null) => (value === null ? sum : (sum ?? 0) + value)
  for (const item of items) {
    const p = points.get(item.meal_date)
    if (!p) continue
    const cost = num(item.cost_total)
    p.meals += 1
    p.energy = add(p.energy, num(item.energy_kcal))
    p.target = add(p.target, num(item.target_energy_kcal))
    p.cost = add(p.cost, cost ?? num(item.known_cost_total))
    p.budget = add(p.budget, num(item.budget))
    p.unknownEnergy ||= num(item.energy_kcal) === null
    p.unknownCost ||= cost === null
  }
  return [...points.values()]
}

export type TrendStats = {
  meals: number
  /** 每餐「實際 ÷ 目標」的平均；沒有可比較的紀錄為 null */
  energyReach: number | null
  proteinReach: number | null
  /** 價格完整的餐數，與它們的平均按比例成本 */
  pricedMeals: number
  averageCost: number | null
  /** 價格未知的餐數（含只知道一部分的） */
  unknownCostMeals: number
  /** 其中只知道一部分價格的餐數 */
  partialCostMeals: number
  /** 價格完整的餐裡，成本不超過預算的餐數 */
  underBudget: number
}

function averageRatio(items: TrendItem[], actual: keyof TrendItem, target: keyof TrendItem): number | null {
  const ratios: number[] = []
  for (const item of items) {
    const a = num(item[actual] as Numeric)
    const t = num(item[target] as Numeric)
    // 目標是 0 或未知就沒辦法比
    if (a !== null && t) ratios.push(a / t)
  }
  return ratios.length ? ratios.reduce((s, r) => s + r, 0) / ratios.length : null
}

export function trendStats(items: TrendItem[]): TrendStats {
  const costs = items.map((i) => ({ cost: num(i.cost_total), known: num(i.known_cost_total), budget: num(i.budget) }))
  const priced = costs.filter((c) => c.cost !== null)
  const total = priced.reduce((s, c) => s + (c.cost as number), 0)
  return {
    meals: items.length,
    energyReach: averageRatio(items, 'energy_kcal', 'target_energy_kcal'),
    proteinReach: averageRatio(items, 'protein_g', 'target_protein_g'),
    pricedMeals: priced.length,
    averageCost: priced.length ? total / priced.length : null,
    unknownCostMeals: items.length - priced.length,
    partialCostMeals: costs.filter((c) => c.cost === null && c.known !== null).length,
    underBudget: priced.filter((c) => c.budget !== null && (c.cost as number) <= c.budget).length,
  }
}

const valueOf = (p: DayPoint, kind: TrendKind) => (kind === 'cost' ? p.cost : p.energy)
const refOf = (p: DayPoint, kind: TrendKind) => (kind === 'cost' ? p.budget : p.target)
const unknownOf = (p: DayPoint, kind: TrendKind) => (kind === 'cost' ? p.unknownCost : p.unknownEnergy)

/** 長條圖的刻度上緣：比最大值（含目標／預算）高 15%，取到十位。沒有任何已知值時給 10，避免除以 0。 */
export function scaleTop(points: DayPoint[], kind: TrendKind): number {
  let max = 0
  for (const p of points) max = Math.max(max, valueOf(p, kind) ?? 0, refOf(p, kind) ?? 0)
  return max > 0 ? Math.ceil((max * 1.15) / 10) * 10 : 10
}

export type BarView = {
  /** 長條高度佔刻度的百分比；未知為 null（不畫長條） */
  height: number | null
  /** 目標／預算刻度的位置；沒有為 null */
  tick: number | null
  /** 只算了已知的部分 */
  partial: boolean
}

/** 這天的數字只算了已知的部分（有幾餐未知）；整天都未知不算，那種不畫長條。 */
function isPartial(p: DayPoint, kind: TrendKind): boolean {
  return valueOf(p, kind) !== null && unknownOf(p, kind)
}

export function barView(p: DayPoint, kind: TrendKind, top: number): BarView {
  const value = valueOf(p, kind)
  const ref = refOf(p, kind)
  const pct = (v: number) => Math.min(100, Math.max(0, (v / top) * 100))
  return {
    height: value === null ? null : pct(value),
    tick: ref === null ? null : pct(ref),
    partial: isPartial(p, kind),
  }
}

/**
 * 圖上有沒有「只計已知部分」的長條。成本和熱量都會有：同一天一餐已知、一餐未知，長條只有已知那一餐的高度。
 * 有的話圖例和說明要寫出來，不然會被當成整天的數字。
 */
export function hasPartialBars(points: DayPoint[], kind: TrendKind): boolean {
  return points.some((p) => isPartial(p, kind))
}

/** 一天的文字說明（長條的名稱與提示）：日期、餐數、數值（未知就寫未知）、目標或預算。 */
export function describePoint(p: DayPoint, kind: TrendKind): string {
  const value = valueOf(p, kind)
  const ref = refOf(p, kind)
  const unit = kind === 'cost' ? '元' : 'kcal'
  const amount = (v: number) => `${Math.round(v).toLocaleString('en-US')} ${unit}`
  const parts = [
    `${p.label}・${p.meals} 餐`,
    `${kind === 'cost' ? '按比例成本' : '熱量'} ${value === null ? '未知' : amount(value)}${value !== null && unknownOf(p, kind) ? '（僅已知部分）' : ''}`,
  ]
  if (ref !== null) parts.push(`${kind === 'cost' ? '預算' : '目標'} ${amount(ref)}`)
  return parts.join('，')
}

/** 時間軸下方要標哪幾天：7 天全標；更長只標頭、中、尾。 */
export function axisLabels(points: DayPoint[]): string[] {
  if (points.length <= 7) return points.map((p) => p.label)
  return [points[0], points[Math.floor((points.length - 1) / 2)], points[points.length - 1]].map((p) => p.label)
}

// ---- 已採用的餐單 ----

type GenDish = GenerationPlanDetail['dishes'][number]

export type PlanDish = { dish_name: string; standard_dish_id?: string; recipe_id?: string | null }
  & Partial<Pick<GenDish, 'aliases' | 'cooking_method' | 'steps' | 'steps_display' | 'ingredients' | 'sodium_mg_per_serving' | 'high_sodium' | 'recipe_source'>>

/**
 * GET /meal-plans?date= 的一筆紀錄。後端對兩種採用方式回同一個端點：
 * 資料庫菜單（result_version 為 null，內容在 snapshot）與生成結果（有 result_version，內容在 nutrition／prices…）。
 * 欄位都取自 api/types.ts 的 MealPlanDetail 與 GenerationPlanDetail。
 */
export type AdoptedPlan = {
  id: string
  meal_date: string
  meal_type: MealType
  mode: string
  status?: string
  adopted_at: string
  meal_request_id: string | null
  result_version?: number | null
  market_shortages_available?: boolean
  energy_kcal?: string | null
  protein_g?: string | null
  mode_label?: string
  ai_generated?: boolean
  price_exception_accepted?: boolean
  fallback_message?: string | null
  dishes: PlanDish[]
  snapshot?: MealPlanDetail['snapshot']
  /** 生成結果的主食（後端的紀錄明細有回，api/types.ts 的 GenerationPlanDetail 漏列）；資料庫菜單的在 snapshot.staple */
  staple?: MealResult['staple']
  nutrition?: MealResult['nutrition']
  prices?: MealResult['prices']
  knowledge?: MealResult['knowledge']
  previous_day?: GenerationPlanDetail['previous_day']
}

export function isGenerationPlan(plan: AdoptedPlan): boolean {
  return plan.result_version !== null && plan.result_version !== undefined
}

/**
 * 「一鍵購入」出現的條件（和舊前端相同）：
 * - 生成結果：這份餐單仍是採用中的那一份，而且有缺料清單（待買或無法換算的食材）。
 * - 資料庫菜單：採用中，且後端標示有保存缺料快照。
 */
export function canCheckout(plan: AdoptedPlan): boolean {
  if (plan.status !== undefined && plan.status !== 'adopted') return false
  if (!isGenerationPlan(plan)) return plan.market_shortages_available === true
  return Boolean(plan.prices?.items?.length || plan.prices?.unquantified_items?.length)
}

/** 回餐桌看這一版（唯讀）的網址；只有生成結果的紀錄有對應的那一桌。 */
export function tablePath(plan: AdoptedPlan): string | null {
  if (!isGenerationPlan(plan) || !plan.meal_request_id) return null
  return `/plan/${plan.meal_request_id}/result/${plan.result_version}`
}

/** 整餐熱量：生成結果在 nutrition，資料庫菜單在快照的 totals（摘要欄位是同一個值）。未知為 null。 */
export function planEnergy(plan: AdoptedPlan): number | null {
  if (isGenerationPlan(plan)) return num(plan.nutrition?.energy_kcal)
  return num(plan.snapshot?.nutrition?.totals?.energy_kcal ?? plan.energy_kcal)
}

/**
 * 便利貼上列的菜名，主食排最前面。主食不在菜色清單裡，要另外補：
 * 生成結果的在 staple.label，資料庫菜單的在快照裡。整餐熱量有把主食算進去，菜名漏掉它就對不起來。
 */
export function planDishNames(plan: AdoptedPlan): string[] {
  const names = plan.dishes.map((d) => d.dish_name)
  const staple = isGenerationPlan(plan) ? plan.staple?.label : plan.snapshot?.staple?.name ?? plan.snapshot?.staple_choice
  return staple && !names.includes(staple) ? [staple, ...names] : names
}

/**
 * 物件列的「當日餐單」按下去做什麼：只有一餐直接開明細；好幾餐開清單讓人選；沒有就把焦點帶到便利貼（上面有去別天的按鈕）。
 * 好幾餐時不能只把焦點帶到便利貼：手機畫面矮的時候它要捲才看得到，從物件列一定要點得到每一餐。
 */
export type DayEntry = { kind: 'note' } | { kind: 'plan'; id: string } | { kind: 'day' }
export function dayEntry(plans: readonly Pick<AdoptedPlan, 'id'>[]): DayEntry {
  if (plans.length === 0) return { kind: 'note' }
  return plans.length === 1 ? { kind: 'plan', id: plans[0].id } : { kind: 'day' }
}

export function planTitle(plan: AdoptedPlan): string {
  return `${dayLabel(plan.meal_date)} ${MEAL_LABELS[plan.meal_type] ?? plan.meal_type}`
}

// ---- 生成紀錄（問卷清單） ----

/** 點一列去哪裡：食譜庫選菜回食譜書，其餘回出菜口的單（和舊的「我的問卷」相同）。 */
export function requestPath(request: Pick<MealRequestSummary, 'id' | 'mode'>): string {
  return request.mode === 'picker' ? `/pick/${request.id}` : `/plan/${request.id}`
}

export function requestTitle(request: Pick<MealRequestSummary, 'meal_date' | 'meal_type'>): string {
  const date = request.meal_date ? `${dayLabel(request.meal_date)}（${weekdayLabel(request.meal_date)}）` : '未選日期'
  return `${date}${request.meal_type ? MEAL_LABELS[request.meal_type] : '未選餐別'}`
}

export function requestNote(request: Pick<MealRequestSummary, 'mode' | 'version'>): string {
  return request.mode === 'picker' ? '食譜庫選菜' : `草稿第 ${request.version} 版`
}
