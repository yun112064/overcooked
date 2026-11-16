/**
 * 冰箱門上兩張便利貼的資料（DESIGN.md 5.2）。用的是舊「已採用紀錄」「營養與採購紀錄」兩頁的同一批端點。
 *
 *   const notes = useFridgeNotes()
 *   notes.adopted   最近採用的那一天與當天各餐的菜名
 *   notes.week      近 7 天：採用餐數、熱量達成、平均每餐的按比例成本
 *
 * 每一張各有 loading／error 狀態；讀不到時便利貼上寫字說明，不會整個廚房壞掉。
 */
import { useQuery } from '@tanstack/react-query'
import { localDate } from '../../session/queries'
import { latestAdoptedDate, planLine, trendItems, weekStats, type WeekStats } from './kitchenLogic'
import { planDatesQuery, plansOnDateQuery, weekTrendQuery } from './kitchenQueries'

export type AdoptedNote =
  | { status: 'loading' | 'error' | 'empty' }
  /** lines 為 null：日期知道了，但當天的菜色還在讀或讀不到 */
  | { status: 'ready'; date: string; lines: { meal: string; dishes: string }[] | null; linesError: boolean }

export type WeekNote = { status: 'loading' | 'error' } | ({ status: 'ready' } & WeekStats)

export function useFridgeNotes(): { adopted: AdoptedNote; week: WeekNote } {
  const today = localDate()
  const dates = useQuery(planDatesQuery)
  const date = dates.data ? latestAdoptedDate(dates.data, today) : null
  const plans = useQuery({ ...plansOnDateQuery(date ?? ''), enabled: date !== null })
  const trend = useQuery(weekTrendQuery(today))

  let adopted: AdoptedNote
  if (dates.isPending) adopted = { status: 'loading' }
  else if (dates.isError) adopted = { status: 'error' }
  else if (date === null) adopted = { status: 'empty' }
  else adopted = { status: 'ready', date, lines: plans.data ? plans.data.map(planLine) : null, linesError: plans.isError }

  let week: WeekNote
  if (trend.isPending) week = { status: 'loading' }
  else if (trend.isError) week = { status: 'error' }
  else week = { status: 'ready', ...weekStats(trendItems(trend.data)) }

  return { adopted, week }
}
