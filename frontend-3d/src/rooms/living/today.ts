/**
 * 客廳（今日）用到的純邏輯：電視上的餐別與近 7 天熱量、茶几上今天採用的那一餐。
 * 不碰網路、不碰畫面；規則和舊前端的「已採用紀錄」頁一致（byDay、未知不當成 0）。
 */
import { toNumber } from '../../api/nutrition'
import type { GenerationPlanDetail, MealPlanDetail, MealType } from '../../api/types'
import { nutritionVerdict, ratioOf, ratioText } from '../../components/nutritionVerdict'

export const MEALS: readonly MealType[] = ['breakfast', 'lunch', 'dinner']
export const TREND_DAYS = 7
const WEEKDAYS = '日一二三四五六'

/** 網址上的 ?meal=；不認得的值當作沒寫。 */
export function parseMeal(value: string | null | undefined): MealType | null {
  return MEALS.find((meal) => meal === value) ?? null
}

/** 網址沒指定餐別時，依現在幾點猜最想看的那一餐：10 點前早餐、15 點前午餐、之後晚餐。 */
export function defaultMeal(hour: number): MealType {
  if (hour < 10) return 'breakfast'
  return hour < 15 ? 'lunch' : 'dinner'
}

/** 物件列的「電視」一次換一台：早 → 午 → 晚 → 早。 */
export function nextMeal(meal: MealType): MealType {
  return MEALS[(MEALS.indexOf(meal) + 1) % MEALS.length]
}

// ---- 電視：近 7 天熱量 ----

/** 後端的數值：生成結果的紀錄是精確字串（可能是分數 a/b），資料庫菜單的紀錄是數字；未知是 null。 */
type Amount = string | number | null | undefined

export type TrendItem = {
  meal_date: string
  meal_type: string
  energy_kcal: Amount
  protein_g: Amount
  target_energy_kcal: Amount
  target_protein_g: Amount
}

export type Trend = { since: string; until: string; items: TrendItem[] }

function amount(value: Amount): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  return toNumber(value)
}

/**
 * 從快取的趨勢資料取出每一筆。
 * 舊前端同一個 query key 存過兩種形狀（紀錄頁存整包、規劃頁只存 items），
 * 這裡兩種都收，別的房間寫成哪一種客廳都不會壞。
 */
export function trendItems(data: unknown): TrendItem[] {
  if (Array.isArray(data)) return data as TrendItem[]
  const items = (data as { items?: unknown } | null | undefined)?.items
  return Array.isArray(items) ? (items as TrendItem[]) : []
}

/** YYYY-MM-DD 往前或往後幾天（本機日曆，不經過 UTC）。 */
export function shiftDate(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  const date = new Date(y, m - 1, d + days)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

/** 2026-11-14 → 「11/14（六）」 */
export function dateLabel(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return `${m}/${d}（${WEEKDAYS[new Date(y, m - 1, d).getDay()]}）`
}

/**
 * 電視上的一根長條＝某一天、目前餐別。
 * none：那天沒有採用這一餐（不畫長條）；unknown：有採用但熱量或目標算不出來（斜紋，不是 0）；
 * pass／over：算得出達成率，over 是超過目標 110%。
 */
export type DayBar = {
  date: string
  /** 長條下面的字：週幾（一個字），今天寫「今天」 */
  label: string
  today: boolean
  meals: number
  /** 那天這個餐別的熱量合計；只要有一筆未知就是 null（不拿已知的部分充數） */
  energy: number | null
  target: number | null
  /** 熱量 ÷ 目標；算不出來是 null */
  ratio: number | null
  state: 'none' | 'unknown' | 'pass' | 'over'
}

/** 到 today 為止連續 days 天、指定餐別的長條（舊到新）。沒紀錄的日子也留一格，時間軸才連續。 */
export function dayBars(items: readonly TrendItem[], meal: MealType, today: string, days: number = TREND_DAYS): DayBar[] {
  const bars: DayBar[] = []
  for (let back = days - 1; back >= 0; back--) {
    const date = shiftDate(today, -back)
    const [y, m, d] = date.split('-').map(Number)
    const own = items.filter((item) => item.meal_date === date && item.meal_type === meal)
    const sum = (key: 'energy_kcal' | 'target_energy_kcal'): number | null => {
      let total = 0
      for (const item of own) {
        const value = amount(item[key])
        if (value === null) return null
        total += value
      }
      return own.length ? total : null
    }
    const energy = sum('energy_kcal')
    const target = sum('target_energy_kcal')
    const ratio = ratioOf(energy, target)
    const verdict = nutritionVerdict('energy_kcal', ratio)
    bars.push({
      date, today: back === 0, meals: own.length, energy, target, ratio,
      label: back === 0 ? '今天' : WEEKDAYS[new Date(y, m - 1, d).getDay()],
      state: own.length === 0 ? 'none' : verdict === 'unknown' ? 'unknown' : verdict === 'pass' ? 'pass' : 'over',
    })
  }
  return bars
}

/** 圖表最上緣代表的達成率：至少 125%，讓 100% 的目標線和 110% 的上限都在圖裡；有更高的就跟著拉高。 */
export function barScale(bars: readonly DayBar[]): number {
  return Math.max(1.25, ...bars.map((bar) => bar.ratio ?? 0))
}

/** 達成率在圖上的高度（0–100，百分比）。 */
export function barHeight(ratio: number, scale: number): number {
  return Math.round(Math.min(1, Math.max(0, ratio / scale)) * 1000) / 10
}

/** 一根長條的文字說明（給讀屏；畫面上的長條只是輔助）。 */
export function dayText(bar: DayBar): string {
  const day = dateLabel(bar.date)
  if (bar.state === 'none') return `${day}：沒有採用紀錄`
  if (bar.ratio === null) {
    if (bar.energy === null) return `${day}：熱量未知，計算不完整`
    return `${day}：熱量 ${Math.round(bar.energy)} kcal，沒有目標可以比較`
  }
  return `${day}：熱量 ${Math.round(bar.energy ?? 0)} kcal，目標 ${Math.round(bar.target ?? 0)} kcal，達成 ${ratioText('energy_kcal', bar.ratio)}，${bar.state === 'over' ? '超出' : '通過'}`
}

/** 電視右上角那一句：今天這一餐的達成率。 */
export function todayText(bars: readonly DayBar[]): string {
  const bar = bars.find((item) => item.today)
  if (!bar || bar.state === 'none') return '今天沒有紀錄'
  if (bar.ratio === null) return '今天 未知'
  return `今天 ${ratioText('energy_kcal', bar.ratio)}`
}

// ---- 茶几：今天採用的那一餐 ----

/**
 * GET /meal-plans?date= 的一筆。後端有兩種快照：
 * 採用生成結果的有 nutrition（整餐合計與目標）；採用資料庫菜單（含食譜庫選菜）的把合計放在 snapshot 裡。
 * 只有菜名的最小紀錄兩邊都沒有，營養就是未知。
 */
export type TodayPlan = {
  id: string
  meal_date: string
  meal_type: MealType
  adopted_at: string
  dishes: { dish_name: string }[]
  energy_kcal?: Amount
  protein_g?: Amount
  nutrition?: Pick<NonNullable<GenerationPlanDetail['nutrition']>, 'energy_kcal' | 'protein_g' | 'target'> | null
  /** 生成結果的主食（不在 dishes 裡）；沒有主食是 null */
  staple?: { label: string } | null
  snapshot?: Pick<MealPlanDetail['snapshot'], 'targets' | 'nutrition' | 'staple' | 'staple_choice'> | null
}

/** 今天指定餐別採用的那一餐。同一餐別有好幾筆時顯示最後採用的，others 是其餘筆數。 */
export function planForMeal<P extends TodayPlan>(plans: readonly P[], meal: MealType): { plan: P | null; others: number } {
  const own = plans.filter((plan) => plan.meal_type === meal)
  if (own.length === 0) return { plan: null, others: 0 }
  const latest = own.reduce((best, plan) => (Date.parse(plan.adopted_at) >= Date.parse(best.adopted_at) ? plan : best))
  return { plan: latest, others: own.length - 1 }
}

/** 整餐的熱量與蛋白質達成率（實際 ÷ 目標）；任何一邊未知就是 null。 */
export function planRatios(plan: TodayPlan): { energy: number | null; protein: number | null } {
  if (plan.nutrition) {
    const { energy_kcal, protein_g, target } = plan.nutrition
    return { energy: ratioOf(energy_kcal, target?.energy_kcal), protein: ratioOf(protein_g, target?.protein_g) }
  }
  const totals = plan.snapshot?.nutrition?.totals
  const targets = plan.snapshot?.targets
  return {
    energy: ratioOf(totals?.energy_kcal ?? plan.energy_kcal, targets?.energy_kcal),
    protein: ratioOf(totals?.protein_g ?? plan.protein_g, targets?.protein_g),
  }
}

/**
 * 這一餐的菜名。主食不在菜色清單裡，要另外補上（和舊的結果頁、紀錄頁一樣）：
 * 生成結果的主食在 staple.label；資料庫菜單的在快照裡（有算出主食才補，選「不需要主食」就沒有）。
 */
export function dishNames(plan: TodayPlan): string[] {
  const names = plan.dishes.map((dish) => dish.dish_name)
  const saved = plan.snapshot?.staple
  const stapleName = plan.staple?.label ?? (saved ? saved.name ?? plan.snapshot?.staple_choice : null)
  if (stapleName && !names.includes(stapleName)) names.push(stapleName)
  return names
}

/** 菜名排成一行；太多道時只列前幾道，並說明總共幾道。 */
export function dishLine(names: readonly string[], max = 5): string {
  if (names.length <= max) return names.join('、')
  return `${names.slice(0, max).join('、')} 等 ${names.length} 道`
}
