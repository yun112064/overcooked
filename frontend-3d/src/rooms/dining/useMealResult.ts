/**
 * 餐桌要的資料與命令（端點、請求內容、query key、冪等鍵都和舊前端的 MealResultView、GenerationPanel、
 * MarketCheckoutButton 相同）。
 *
 * - 版本清單 ['meal-results', id]、單一版本 ['meal-result', id, version]；重試（查價／小知識）進行中每 2 秒更新一次。
 * - 重試完成後後端會產生新版本：重新讀清單，而且如果是在這個畫面上按的、或看著它跑完的，就自動換到新版本。
 * - 決定、重試、採用都帶 Idempotency-Key：同一個（結果版本、命令）固定一把，重複點擊或網路重送只會成立一次。
 *   例外：上一次重試已經失敗時換一把（retryCommandName），不然後端只會回同一個失敗的工作，再按也不會重試。
 * - 採用資格、版本、缺價接受與昨日歷史一律由後端在交易內重驗；前端只送出指定的版本。
 * - 問卷正在（重新）生成時每 2 秒看一次問卷：煮好了桌子才會解鎖，並重新讀版本清單（會多一版）。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useEffectEvent, useRef } from 'react'
import { useNavigate } from 'react-router'
import { api, apiError, errorStatus } from '../../api/client'
import type { DecisionKind, Job, MealPlanSummary, MealRequest, MealResult, MealResultSummary } from '../../api/types'
import { invalidateAdoptedPlans, mealRequestQuery, mealRequestsQuery } from '../../session/queries'
import { ACTIVE, adoptBody, producedVersion, retryActive, retryCommandName } from './resultText'
import { isActivePlan } from './isActivePlan'
import { adoptionOf, type Adoption } from './tableModel'

/**
 * 冪等鍵。crypto.randomUUID 只在安全來源（https、localhost）才有；
 * 用區網 IP 連開發機測手機時退回自己組一把，不然按鈕一按就丟例外。
 */
function newKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

export function useMealResult(requestId: string, version: number | null) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const keys = useRef(new Map<string, string>())
  const keyFor = (name: string) => {
    if (!keys.current.has(name)) keys.current.set(name, newKey())
    return keys.current.get(name)!
  }

  // 問卷本身：餐期（找已採用紀錄用）、目前版本與是否填完（換一桌要用）、是不是正在煮。
  // 正在煮的時候要一直看：這個畫面上的命令這時全部停用，沒有別的東西會讓它重讀，不看就永遠停在「正在重新煮」
  const request = useQuery({
    ...mealRequestQuery(requestId),
    refetchInterval: (query) => (ACTIVE.has(query.state.data?.status ?? '') ? 2000 : false),
  })
  // 問卷正在（重新）生成：後端這時不收採用、決定與重試
  const cooking = !!request.data && ACTIVE.has(request.data.status)
  const list = useQuery({
    queryKey: ['meal-results', requestId],
    queryFn: async () =>
      (await api.get<{ results: MealResultSummary[] }>(`/meal-requests/${requestId}/results`)).data.results,
  })
  const resultQuery = (v: number | null) => ({
    queryKey: ['meal-result', requestId, v],
    queryFn: async () => (await api.get<MealResult>(`/meal-requests/${requestId}/results/${v}`)).data,
  })
  const result = useQuery({
    ...resultQuery(version),
    enabled: version !== null,
    // 重試工作（查價中／小知識產生中）進行時定期更新，完成後會出現新版本
    refetchInterval: (query) => {
      const retries = query.state.data?.retries
      return retries && (retryActive(retries.price) || retryActive(retries.knowledge)) ? 2000 : false
    },
  })

  const retries = result.data?.retries
  // 只有「在這個畫面上按了重試、或看著重試在跑」才自動換版本；之後再回來看舊版本時不會被硬帶走
  const watching = useRef(false)
  // 離開餐桌之後就不要再換版本（下面會等新版本讀完才換，等的時候人可能已經走了）
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])
  const switchTo = useEffectEvent((produced: number) => {
    // 先把新版本讀進快取再換過去，桌子才不會先空一下；讀失敗也照樣換（到了那一版會顯示錯誤與重試）
    void queryClient.prefetchQuery(resultQuery(produced)).finally(() => {
      if (alive.current) navigate(`/plan/${requestId}/result/${produced}`, { replace: true })
    })
  })
  useEffect(() => {
    if (retryActive(retries?.price) || retryActive(retries?.knowledge)) {
      watching.current = true
      return
    }
    const produced = producedVersion(retries, version)
    if (produced === null) return
    void queryClient.invalidateQueries({ queryKey: ['meal-results', requestId] })
    if (watching.current) {
      watching.current = false
      switchTo(produced)
    }
  }, [retries, version, queryClient, requestId])

  // 煮好（或沒煮成）的那一刻：版本清單可能多了一版，桌上這一版的採用資格也跟著問卷狀態變
  const wasCooking = useRef(false)
  useEffect(() => {
    if (cooking) {
      wasCooking.current = true
      return
    }
    if (!wasCooking.current) return
    wasCooking.current = false
    void queryClient.invalidateQueries({ queryKey: ['meal-results', requestId] })
    void queryClient.invalidateQueries({ queryKey: ['meal-result', requestId] })
  }, [cooking, queryClient, requestId])

  // 登入過期或還沒選家庭：重新確認身分，路由的守門會把人帶回大門或玄關
  const fatal = list.error ?? result.error
  useEffect(() => {
    if (!fatal) return
    if (errorStatus(fatal) === 401 || apiError(fatal)?.code === 'HOUSEHOLD_NOT_SELECTED') {
      void queryClient.invalidateQueries({ queryKey: ['me'] })
    }
  }, [fatal, queryClient])

  const refreshAll = async () => {
    await queryClient.invalidateQueries({ queryKey: ['meal-results', requestId] })
    await queryClient.invalidateQueries({ queryKey: ['meal-result', requestId] })
    // 問卷狀態會跟著變（待決定 → 已有結果 → 已採用），廚房與客廳也讀同一份
    void queryClient.invalidateQueries({ queryKey: mealRequestQuery(requestId).queryKey })
    void queryClient.invalidateQueries({ queryKey: mealRequestsQuery.queryKey })
  }

  const decide = useMutation({
    mutationFn: async ({ r, kind }: { r: MealResult; kind: DecisionKind }) =>
      api.post(`/meal-requests/${requestId}/results/${r.result_version}/decisions`,
        { kind, request_version: r.request_version },
        { headers: { 'Idempotency-Key': keyFor(`${r.result_version}:${kind}`) } }),
    onSuccess: refreshAll,
  })
  // 重試很快就跑完時，重新讀到的可能已經是「成功」而沒看到「進行中」；
  // 先記下是在這裡按的（onMutate），完成後才會自動換到新版本
  const retryPrice = useMutation({
    mutationFn: async (r: MealResult) =>
      api.post(`/meal-requests/${requestId}/results/${r.result_version}/price-retry`,
        { request_version: r.request_version },
        { headers: { 'Idempotency-Key': keyFor(retryCommandName(r.result_version, 'retry', r.retries?.price)) } }),
    onMutate: () => { watching.current = true },
    onError: () => { watching.current = false },
    onSuccess: refreshAll,
  })
  const retryKnowledge = useMutation({
    mutationFn: async (r: MealResult) =>
      api.post(`/meal-requests/${requestId}/results/${r.result_version}/knowledge-retry`,
        { request_version: r.request_version },
        { headers: { 'Idempotency-Key': keyFor(retryCommandName(r.result_version, 'knowledge-retry', r.retries?.knowledge)) } }),
    onMutate: () => { watching.current = true },
    onError: () => { watching.current = false },
    onSuccess: refreshAll,
  })
  const adopt = useMutation({
    mutationFn: async (r: MealResult) =>
      (await api.post<{ meal_plan: MealPlanSummary }>('/meal-plans', adoptBody(requestId, r),
        { headers: { 'Idempotency-Key': keyFor(`${r.result_version}:adopt`) } })).data.meal_plan,
    onSuccess: async () => {
      await refreshAll()
      // 冰箱門、廚房便利貼與客廳的已採用紀錄、趨勢（三個採用入口共用同一個清單）
      void invalidateAdoptedPlans(queryClient)
    },
  })
  // 換一桌＝舊前端的「重新生成」：同一版問卷再送一次。這是新的命令，所以每次都換一把冪等鍵。
  const regenerate = useMutation({
    mutationFn: async (row: MealRequest) =>
      (await api.post<{ job: Job }>(`/meal-requests/${requestId}/submit`, { version: row.version },
        { headers: { 'Idempotency-Key': newKey() } })).data.job,
    onSuccess: async (job) => {
      // 單板讀的是 ['jobs', 問卷 id]（最新一筆生成工作）：先放進去，鏡頭轉過去時鍋蓋才會直接蓋上
      queryClient.setQueryData(['jobs', requestId], job)
      // 廚房與客廳讀的是整個家的清單 ['jobs']（只更新那一份，不動剛放進去的這一筆）
      void queryClient.invalidateQueries({ queryKey: ['jobs'], exact: true })
      await queryClient.invalidateQueries({ queryKey: mealRequestQuery(requestId).queryKey })
      void queryClient.invalidateQueries({ queryKey: mealRequestsQuery.queryKey })
      if (ACTIVE.has(job.status)) {
        navigate(`/plan/${requestId}/cooking`)
        return
      }
      // 送出後馬上就結束的少數情況：有新版本就去看；失敗則留在原地把原因寫出來（regenerateFailure）
      void queryClient.invalidateQueries({ queryKey: ['meal-results', requestId] })
      const produced = job.status === 'succeeded' ? job.result_summary?.result_version ?? job.result_version : null
      if (produced != null) navigate(`/plan/${requestId}/result/${produced}`, { replace: true })
    },
  })
  const failedJob = regenerate.data?.status === 'failed' ? regenerate.data : null

  const shown = result.data
  const mealDate = request.data?.draft.meal_date ?? null
  const confirmed = shown?.request_status === 'confirmed'
  const hasShortages = Boolean(shown?.prices.items.length || shown?.prices.unquantified_items?.length)
  /**
   * 這份問卷的已採用紀錄：一鍵購入要它的 id，也要知道採用的是哪一版（已採用的問卷，其餘版本只能看）。
   * 剛採用完用回傳的那一筆，之後再進來時用餐期去找。
   * 舊前端只在有缺料時才讀；這裡已採用就讀，因為還要分辨「現在看的是不是被採用的那一版」。
   */
  const adoptedPlans = useQuery({
    queryKey: ['sim-market-adopted-plan', requestId, mealDate],
    enabled: confirmed && Boolean(mealDate) && !adopt.data,
    queryFn: async () =>
      (await api.get<{ meal_plans: MealPlanSummary[] }>('/meal-plans', { params: { date: mealDate } })).data.meal_plans,
  })
  const adoptedPlan = adopt.data
    ?? adoptedPlans.data?.find((plan) => isActivePlan(plan, requestId))
  // 「紀錄還沒讀到」和「採用的是資料庫菜單（紀錄上沒有結果版本）」是兩回事，交給 adoptionOf 分清楚
  const adoption: Adoption = shown ? adoptionOf(shown, adoptedPlan) : { kind: 'none' }
  const adoptedPlanId = adoptedPlan && adoption.kind === 'this' ? adoptedPlan.id : undefined

  return {
    request, cooking, list, result, latestVersion: list.data?.[0]?.result_version,
    decide, retryPrice, retryKnowledge, adopt, regenerate,
    /** 換一桌送出後工作立刻失敗時的原因（後端給的文字）；沒有失敗是 null */
    regenerateFailure: failedJob ? failedJob.error?.message ?? '這次沒有煮成，原因未知。' : null,
    mealDate, mealType: request.data?.draft.meal_type ?? null,
    confirmed, hasShortages, adoptedPlans, adoptedPlanId,
    /** 這份問卷的採用和現在看的這一版的關係（就是這一版／別的版本／資料庫菜單／還不知道／沒採用） */
    adoption,
  }
}

export type MealResultState = ReturnType<typeof useMealResult>

/**
 * 一鍵購入（舊前端的 MarketCheckoutButton）：請後端開一個模擬超市的結帳網址，在新分頁打開。
 * 要在點擊當下同步開分頁，等 API 回來才開會被瀏覽器當成彈出視窗擋掉。
 */
export function useMarketCheckout(planId: string | undefined) {
  const handoff = useMutation({
    mutationFn: async (id: string) => {
      const data = (await api.post<{ checkout_url: string }>('/sim-market/handoffs', { plan_id: id })).data
      const url = new URL(data.checkout_url)
      // 只接受一般網址；後端回了別的協定（javascript: 之類）就當作失敗
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid checkout URL')
      return url.href
    },
  })
  const open = async () => {
    if (!planId || handoff.isPending) return
    const tab = window.open('about:blank', '_blank')
    if (tab) tab.opener = null
    try {
      const url = await handoff.mutateAsync(planId)
      if (tab && !tab.closed) tab.location.replace(url)
      else window.location.assign(url)
    } catch {
      tab?.close()
    }
  }
  return { open, isPending: handoff.isPending, isError: handoff.isError, error: handoff.error }
}
