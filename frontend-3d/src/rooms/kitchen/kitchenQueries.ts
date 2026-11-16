/**
 * 廚房讀的資料。查詢本體都在 session/queries.ts（客廳、冰箱門共用同一份快取）；這裡只指定廚房要讀的欄位。
 *
 *   generationJobsQuery      這個家庭的生成工作（新到舊）——決定鍋是不是在冒煙
 *   mealResultsQuery(id)     一份問卷的結果版本（新到舊）——上桌前要知道最新是第幾版
 *   weekTrendQuery(today)    近 7 天已採用餐食的熱量與成本——冰箱便利貼
 *   planDatesQuery           有已採用紀錄的日期（新到舊）
 *   plansOnDateQuery(date)   某一天已採用的各餐
 */
import type { MealPlanSummary } from '../../api/types'
import { plansOnDateQuery as sharedPlansOnDate, trendQuery, type TrendResponse as SharedTrend } from '../../session/queries'
import type { TrendItem } from './kitchenLogic'

export { generationJobsQuery, mealResultsQuery, planDatesQuery } from '../../session/queries'

export type TrendResponse = SharedTrend<TrendItem>

/** 截止日用本機今天：預先安排的未來餐食不列入統計。 */
export function weekTrendQuery(today: string) {
  return trendQuery<TrendItem>(7, today)
}

/** 便利貼只用到餐別與菜名；完整快照由冰箱門那一景自己讀（同一個 key、同一份資料）。 */
export function plansOnDateQuery(date: string) {
  return sharedPlansOnDate<MealPlanSummary>(date)
}
