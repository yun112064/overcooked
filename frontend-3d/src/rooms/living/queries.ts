/**
 * 客廳讀的資料。查詢本體都在 session/queries.ts（廚房、冰箱門共用同一份快取），
 * 所以從冰箱或餐桌回來時可以直接用快取裡的資料；這裡只指定客廳要讀的欄位。
 */
import type { Job } from '../../api/types'
import { plansOnDateQuery, trendQuery as sharedTrend } from '../../session/queries'
import { TREND_DAYS, type TodayPlan, type TrendItem } from './today'

export { generationJobsQuery } from '../../session/queries'

/** 到 today 為止近 7 天每一筆已採用餐食的熱量與目標。 */
export function trendQuery(today: string) {
  return sharedTrend<TrendItem>(TREND_DAYS, today)
}

/** 某一天各餐別已採用的餐食（採用當下保存的快照，只能看）。 */
export function plansOnQuery(date: string) {
  return plansOnDateQuery<TodayPlan>(date)
}

/** 還在排隊或執行中的工作；有的話要繼續追蹤（和舊前端的生成面板一樣每 2 秒問一次）。 */
export function isActiveJob(job: Pick<Job, 'status'>): boolean {
  return job.status === 'queued' || job.status === 'running'
}

export const JOB_POLL_MS = 2000
