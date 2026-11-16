/**
 * 廚房全景用到的純邏輯（不碰網路、不碰畫面），規則都搬自舊前端的「我的問卷」頁（MealRequestsPage）。
 *
 *   ticketText(request)                  單軌上一張單要寫的字（日期、餐別、備註）
 *   railTickets(requests)                哪幾張掛在單軌上、還有幾張掛不下
 *   destinationFor(request, pot)         點一張單或一口鍋要去哪裡
 *   anyCooking(requests, jobs)           有沒有東西在生成中（決定要不要輪詢）
 *   trendItems(data) / weekStats(items)  冰箱便利貼「近 7 天」的三個數字
 *   latestAdoptedDate(dates, today)      冰箱便利貼「最近採用」要看哪一天
 */
import { MEAL_LABELS, type MealRequestSummary, type MealType } from '../../api/types'
import { toNumber } from '../../api/nutrition'
import { ratioOf } from '../../components/nutritionVerdict'
import type { PotJob, PotState } from '../../pot/potState'

const WEEKDAYS = '日一二三四五六'

/** 2026-10-24 → { md: '10/24', weekday: '週六' }；沒有日期或看不懂就回 null。 */
export function dateParts(iso: string | null | undefined): { md: string; weekday: string } | null {
  if (!iso) return null
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  if (![y, m, d].every(Number.isInteger)) return null
  // 用本機時區的年月日建立日期：new Date('2026-10-24') 是 UTC，換算星期可能差一天
  return { md: `${m}/${d}`, weekday: `週${WEEKDAYS[new Date(y, m - 1, d).getDay()]}` }
}

/** 今天的日期標題：11 月 14 日・星期六 */
export function todayTitle(now: Date = new Date()): string {
  return `${now.getMonth() + 1} 月 ${now.getDate()} 日・星期${WEEKDAYS[now.getDay()]}`
}

/** 依本機時間的問候語（和舊前端相同的分界：11 點前早安、17 點前午安）。 */
export function greeting(now: Date = new Date()): string {
  const hour = now.getHours()
  return hour < 11 ? '早安' : hour < 17 ? '午安' : '晚安'
}

export type TicketText = {
  /** 10/24；還沒選日期是「—」 */
  date: string
  /** 週六；還沒選日期是「未選日期」 */
  weekday: string
  /** 午餐；還沒選是「未選餐別」 */
  meal: string
  /** 食譜庫選菜／草稿第 3 版 */
  note: string
}

export function ticketText(request: Pick<MealRequestSummary, 'meal_date' | 'meal_type' | 'mode' | 'version'>): TicketText {
  const date = dateParts(request.meal_date)
  return {
    date: date?.md ?? '—',
    weekday: date?.weekday ?? '未選日期',
    meal: request.meal_type ? MEAL_LABELS[request.meal_type] ?? request.meal_type : '未選餐別',
    note: request.mode === 'picker' ? '食譜庫選菜' : `草稿第 ${request.version} 版`,
  }
}

/** 單軌上掛得下幾張單（第四個位置固定留給「全部的單」）。 */
export const RAIL_TICKETS = 3

/** 清單本來就是新到舊：最新的幾張掛上單軌，其餘只在「全部的單」面板裡。 */
export function railTickets<R>(requests: readonly R[], max: number = RAIL_TICKETS): { shown: R[]; hidden: number } {
  const shown = requests.slice(0, Math.max(0, max))
  return { shown, hidden: requests.length - shown.length }
}

/** path：直接換頁；result：要先查這份問卷最新的結果版本，再到餐桌。 */
export type Destination = { kind: 'path'; to: string } | { kind: 'result'; requestId: string }

/**
 * 點一張單（或它的鍋）要去哪裡。
 * 舊前端只分兩種：食譜庫選菜 → /pick/:id，其餘 → /plan/:id，再由那一頁依狀態顯示問卷、進度或結果。
 * 新前端把那一頁拆成三個網址，所以這裡先依狀態分好：
 *   食譜庫選菜            → /pick/:id（不論狀態，和舊前端一樣）
 *   已採用                → 餐桌（唯讀的結果）
 *   生成中、失敗          → /plan/:id/cooking（看進度、看失敗原因與重試）
 *   有結果（含待決定）    → 餐桌
 *   其餘（草稿）          → /plan/:id
 */
export function destinationFor(request: Pick<MealRequestSummary, 'id' | 'mode' | 'status'>, pot: PotState): Destination {
  if (request.mode === 'picker') return { kind: 'path', to: `/pick/${request.id}` }
  if (request.status === 'confirmed' || pot.kind === 'ready') return { kind: 'result', requestId: request.id }
  if (pot.kind === 'cooking' || pot.kind === 'failed') return { kind: 'path', to: `/plan/${request.id}/cooking` }
  return { kind: 'path', to: `/plan/${request.id}` }
}

/** 查到結果版本之後的網址；這份問卷其實沒有結果（清單過期了）就回到問卷。 */
export function resultPath(requestId: string, results: readonly { result_version: number }[]): string {
  const latest = results[0]?.result_version
  return latest === undefined ? `/plan/${requestId}` : `/plan/${requestId}/result/${latest}`
}

const ACTIVE = new Set(['queued', 'running'])

/** 有問卷或工作在排隊／生成中時要定時重新讀取，鍋才會自己從冒煙變成開蓋。 */
export function anyCooking(
  requests: readonly { status: string }[] | undefined, jobs: readonly Pick<PotJob, 'status'>[] | undefined,
): boolean {
  return (requests ?? []).some((request) => ACTIVE.has(request.status)) || (jobs ?? []).some((job) => ACTIVE.has(job.status))
}

/** 鍋下方標籤的第二行：點了會做什麼。 */
export function potAction(state: PotState, hasRequest: boolean): string {
  switch (state.kind) {
    case 'empty': return hasRequest ? '接著填' : '新的一餐'
    case 'filling': return '接著填'
    case 'full': return '去送出'
    case 'cooking': return '看進度'
    case 'ready': return '上桌看結果'
    case 'failed': return '看原因'
  }
}

// ---- 冰箱便利貼 ----

/** 後端的精確值是字串（可能是 a/b 分數），舊紀錄與資料庫菜單是數字；沒有這個欄位或 null 都是未知。 */
type Exact = string | number | null | undefined

export type TrendItem = {
  meal_date: string
  meal_type: string
  energy_kcal?: Exact
  target_energy_kcal?: Exact
  /** 按比例成本（不是整包花費）；價格不完整時是 null */
  cost_total?: Exact
}

/** GET /meal-plans/trend 的回應；舊前端有一頁只把 items 放進快取，所以兩種形狀都收。 */
export function trendItems(data: { items?: TrendItem[] } | TrendItem[] | null | undefined): TrendItem[] {
  if (!data) return []
  return Array.isArray(data) ? data : data.items ?? []
}

function known(value: Exact): number | null {
  const n = typeof value === 'number' ? value : toNumber(value)
  return n === null || !Number.isFinite(n) ? null : n
}

export type WeekStats = {
  /** 已採用的餐數 */
  meals: number
  /** 熱量達成：各餐「實際 ÷ 目標」的平均；沒有任何一餐算得出來就是 null（未知） */
  energyReach: number | null
  /** 平均每餐的按比例成本（元）；只算價格完整的餐，一餐都沒有就是 null（未知） */
  averageCost: number | null
  /** 價格未知、沒算進平均的餐數 */
  unknownCostMeals: number
}

/** 和舊前端歡迎區的三個數字同一套算法；未知的餐不當成 0，直接不列入平均。 */
export function weekStats(items: readonly TrendItem[]): WeekStats {
  const ratios = items.map((item) => ratioOf(item.energy_kcal, item.target_energy_kcal)).filter((r) => r !== null)
  const costs = items.map((item) => known(item.cost_total)).filter((c) => c !== null)
  const mean = (values: number[]) => (values.length ? values.reduce((sum, v) => sum + v, 0) / values.length : null)
  return {
    meals: items.length,
    energyReach: mean(ratios),
    averageCost: mean(costs),
    unknownCostMeals: items.length - costs.length,
  }
}

/**
 * 便利貼「最近採用」要看哪一天：到今天為止最近的一天；只有預先排好的未來餐時，看最接近今天的那一天。
 * dates 是 GET /meal-plans 的清單（新到舊）。
 */
export function latestAdoptedDate(dates: readonly { meal_date: string }[], today: string): string | null {
  const days = dates.map((d) => d.meal_date).sort()
  const past = days.filter((day) => day <= today)
  return past[past.length - 1] ?? days[0] ?? null
}

/** 一筆已採用餐食在便利貼上的一行：餐別「晚餐」＋菜名「五穀飯、清蒸鱸魚」。 */
export function planLine(plan: { meal_type: MealType | string; dishes: readonly { dish_name: string }[] }): { meal: string; dishes: string } {
  const meal = MEAL_LABELS[plan.meal_type as MealType] ?? plan.meal_type
  return { meal, dishes: plan.dishes.map((dish) => dish.dish_name).join('、') }
}
