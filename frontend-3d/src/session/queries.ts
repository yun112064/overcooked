/**
 * 好幾個房間都會讀的資料，查詢定義集中在這裡：同一個 query key 存的資料形狀一定相同，
 * 一個房間改了資料，其他房間的快取也跟著對（key、端點與形狀都和舊前端一樣）。
 *
 *   const requests = useQuery(mealRequestsQuery)                      // MealRequestSummary[]，新到舊
 *   const request = useQuery(mealRequestQuery(id))                    // MealRequest
 *   const members = useQuery(membersQuery)                            // Member[]
 *   useQuery({ ...mealRequestsQuery, refetchInterval: 2000 })         // 要輪詢或加選項時展開再加
 *   queryClient.invalidateQueries({ queryKey: mealRequestsQuery.queryKey })
 *
 * 只有單一房間用到的查詢寫在那個房間裡就好，不用搬來這裡。
 *
 * 已採用餐食那幾個查詢（plansOnDateQuery、trendQuery）各房間只讀自己需要的欄位，
 * 所以回傳型別由呼叫端指定；快取裡放的永遠是後端回的原樣（整個陣列／整包回應），不要先加工再存。
 * 採用或取消採用之後呼叫 invalidateAdoptedPlans()，客廳、廚房便利貼、冰箱門才會一起更新。
 */
import { queryOptions, type QueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { Job, MealPlanSummary, MealRequest, MealRequestSummary, MealResultSummary, Member } from '../api/types'

/** 這個家庭的問卷清單（後端依更新時間新到舊，最多 50 筆）。 */
export const mealRequestsQuery = queryOptions({
  queryKey: ['meal-requests'],
  queryFn: async () => (await api.get<{ meal_requests: MealRequestSummary[] }>('/meal-requests')).data.meal_requests,
})

/** 單一問卷的完整內容（含 issues、complete、目標預覽）。 */
export function mealRequestQuery(id: string) {
  return queryOptions({
    queryKey: ['meal-request', id],
    queryFn: async () => (await api.get<MealRequest>(`/meal-requests/${id}`)).data,
  })
}

/** 這個家庭的成員。 */
export const membersQuery = queryOptions({
  queryKey: ['members'],
  queryFn: async () => (await api.get<{ members: Member[] }>('/members')).data.members,
})

/**
 * 這個家庭最近的生成工作（新到舊，後端最多回 50 筆）；客廳門口與廚房的鍋用它判斷是不是還在煮。
 * 舊前端只在問卷頁用 ['jobs', 問卷 id] 讀單一問卷最新的一筆；這裡少一層 id，一次看整個家。
 * 要讓它重讀時用 { queryKey: ['jobs'], exact: true }，不然會連每份問卷自己的那一筆一起重讀。
 */
export const generationJobsQuery = queryOptions({
  queryKey: ['jobs'],
  queryFn: async () => (await api.get<{ jobs: Job[] }>('/jobs', { params: { kind: 'meal_generation' } })).data.jobs,
})

/** 一份問卷的結果版本（新到舊）；查價或小知識重試會產生比生成工作更新的版本，上桌前要看這份清單。 */
export function mealResultsQuery(requestId: string) {
  return queryOptions({
    queryKey: ['meal-results', requestId],
    queryFn: async () =>
      (await api.get<{ results: MealResultSummary[] }>(`/meal-requests/${requestId}/results`)).data.results,
  })
}

export type PlanDateCount = { meal_date: string; count: number }

/** 有已採用紀錄的日期與筆數。 */
export const planDatesQuery = queryOptions({
  queryKey: ['meal-plan-dates'],
  queryFn: async () => (await api.get<{ dates: PlanDateCount[] }>('/meal-plans')).data.dates,
})

/** 某一天各餐別已採用的餐食（採用當下保存的快照，只能看）。P 是呼叫端要讀的欄位。 */
export function plansOnDateQuery<P = MealPlanSummary>(date: string) {
  return queryOptions({
    queryKey: ['meal-plans', date],
    queryFn: async () => (await api.get<{ meal_plans: P[] }>('/meal-plans', { params: { date } })).data.meal_plans,
  })
}

export type TrendResponse<I> = { since: string; until: string; items: I[] }

/**
 * 到 today 為止近 days 天每一筆已採用餐食的熱量、目標與成本。
 * until 傳使用者本機的今天：伺服器時區可能不同，而且預先安排的未來餐食不列入。
 * 快取裡是整包回應（舊前端有一頁只存 items；這裡統一存整包）。
 */
export function trendQuery<I>(days: number, today: string) {
  return queryOptions({
    queryKey: ['meal-plan-trend', days, today],
    queryFn: async () => (await api.get<TrendResponse<I>>('/meal-plans/trend', { params: { days, until: today } })).data,
  })
}

export type ServiceState = { status: 'online' | 'offline' | 'not_configured'; reason: string | null }
/** GET /health/team；舊前端只看辨識與知識兩項，小幫手那一項有回才顯示。 */
export type TeamStatus = { recognition: ServiceState; knowledge: ServiceState; assistant?: ServiceState }

/** 團隊電腦上的服務（蔬菜辨識、營養知識）是否在線；和舊前端的生成面板同一個 key。 */
export const teamStatusQuery = queryOptions({
  queryKey: ['team-status'],
  queryFn: async () => (await api.get<TeamStatus>('/health/team')).data,
  staleTime: 30_000,
})

/**
 * 採用了一餐之後：已採用的日期、當日餐單、趨勢、問卷清單都過期了。
 * 三個採用入口（餐桌、單板的資料庫菜單、食譜書）都呼叫這一個，別的房間才不會漏掉。
 */
export function invalidateAdoptedPlans(client: QueryClient): Promise<void> {
  return Promise.all([
    client.invalidateQueries({ queryKey: planDatesQuery.queryKey }),
    client.invalidateQueries({ queryKey: ['meal-plans'] }),
    client.invalidateQueries({ queryKey: ['meal-plan-trend'] }),
    client.invalidateQueries({ queryKey: ['sim-market-adopted-plan'] }),
    client.invalidateQueries({ queryKey: mealRequestsQuery.queryKey }),
  ]).then(() => undefined)
}

/**
 * 使用者本機時區的日期（YYYY-MM-DD）。
 * 不能用 toISOString：那是 UTC，台灣早上 8 點前會變成昨天。
 */
export function localDate(date: Date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
