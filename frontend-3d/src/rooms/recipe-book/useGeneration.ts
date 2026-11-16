/**
 * 送出生成並追蹤工作狀態（和舊前端的 GenerationPanel 相同的端點、query key 與冪等鍵規則）。
 * 放在場景這一層而不是面板裡：面板關掉之後還要繼續追蹤進度。
 *
 * active：只有在「結果」那一步才查；onChanged：工作狀態變了，請場景重新讀取這張單。
 * 工作狀態讀不到時不能就此停住：這張單還鎖著（生成中）的話繼續每 2 秒問，錯誤交給面板寫出來。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'
import { api } from '../../api/client'
import type { Job, MealRequest } from '../../api/types'
import { generationJobsQuery, mealResultsQuery, teamStatusQuery } from '../../session/queries'
import { newKey } from './idempotency'
import { isActiveJob, pollInterval } from './previewText'

export type { ServiceState, TeamStatus } from '../../session/queries'

export function useGeneration(row: MealRequest | null, active: boolean, onChanged: () => void) {
  const queryClient = useQueryClient()
  const id = row?.id ?? ''
  const version = row?.version ?? 0
  // 這張單本身寫著排隊／生成中：就算工作一次都還沒讀到（第一次讀取失敗）也要繼續問
  const requestLocked = row?.status === 'queued' || row?.status === 'running'
  // 同一問卷版本固定使用同一把冪等鍵：網路重送或重複點擊不會建立第二個工作
  const keys = useRef(new Map<number, string>())
  // 廚房與客廳的鍋讀整個家的工作清單、餐桌讀這張單的結果版本；這裡送出、取消、煮完後都要讓它們過期（和單板一樣）。
  // exact：['jobs', id]（這裡自己的）剛寫進最新的工作，不用再問一次
  const touchOtherRooms = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: generationJobsQuery.queryKey, exact: true })
    void queryClient.invalidateQueries({ queryKey: mealResultsQuery(id).queryKey })
  }, [queryClient, id])

  const latest = useQuery({
    queryKey: ['jobs', id],
    queryFn: async () =>
      // 只看生成工作；結果重試（知識說明、定向查價）由餐桌自己追蹤
      (await api.get<{ jobs: Job[] }>('/jobs', { params: { meal_request_id: id, kind: 'meal_generation' } }))
        .data.jobs[0] ?? null,
    enabled: active && !!row,
    refetchInterval: (query) => pollInterval(query.state.data, requestLocked),
  })

  const submit = useMutation({
    mutationFn: async (regenerate: boolean) => {
      // 重新生成是新的命令：換一把冪等鍵，產生新的結果版本
      if (regenerate) keys.current.delete(version)
      const key = keys.current.get(version) ?? newKey()
      keys.current.set(version, key)
      return (await api.post<{ job: Job }>(`/meal-requests/${id}/submit`, { version },
        { headers: { 'Idempotency-Key': key } })).data.job
    },
    onSuccess: (job) => {
      if (job.status === 'failed') keys.current.delete(version)
      queryClient.setQueryData(['jobs', id], job)
      touchOtherRooms()
      onChanged()
    },
  })
  const cancel = useMutation({
    mutationFn: async () => (await api.post<{ job: Job }>(`/meal-requests/${id}/cancel`)).data.job,
    onSuccess: (job) => {
      // 取消後重新送出是新的請求，要換一把冪等鍵
      if (job.input_version !== null) keys.current.delete(job.input_version)
      queryClient.setQueryData(['jobs', id], job)
      touchOtherRooms()
      onChanged()
    },
  })

  const job = latest.data ?? null
  const running = isActiveJob(job)

  // 工作由進行中變成結束時，請場景重新讀取這張單（解除生成中的鎖定）。
  // 另一種情況：這張單還寫著生成中，讀到的工作卻已經結束（一開始讀不到，等讀到時早就煮完了）——
  // 這裡沒看過它「進行中」的樣子，一樣要重讀，不然鎖永遠解不開。每個工作只補這一次。
  const wasRunning = useRef(false)
  const settledJob = useRef<string | null>(null)
  useEffect(() => {
    const finished = wasRunning.current && !running
    const missed = !!job && !running && requestLocked && settledJob.current !== job.id
    if (finished || missed) {
      settledJob.current = job?.id ?? null
      // 失敗後重新送出是新的請求，要換一把冪等鍵；否則只會拿回同一個失敗工作
      if (job?.status === 'failed' && job.input_version !== null) keys.current.delete(job.input_version)
      touchOtherRooms()
      onChanged()
    }
    wasRunning.current = running
  }, [running, job, requestLocked, onChanged, touchOtherRooms])

  const forThisVersion = !!job && job.input_version === version
  // 已採用：仍可查看結果，但不再生成或修改（後端同樣拒絕）
  const confirmed = row?.status === 'confirmed'
  const hasResult = row?.status === 'result_ready' || row?.status === 'awaiting_decision' || confirmed
  const complete = !!row?.complete

  // 最新一版結果的版本號，用來連到餐桌（和餐桌讀的是同一份清單）
  const results = useQuery({
    ...mealResultsQuery(id),
    enabled: active && !!row && hasResult && !running,
  })

  const team = useQuery({ ...teamStatusQuery, enabled: active })

  return {
    job,
    running,
    /** 這張單寫著排隊／生成中（工作還沒讀到時也看這個，才知道鍋是不是還在煮） */
    requestLocked,
    /** 工作狀態讀不到（第一次就失敗，或輪詢到一半斷線）；背景會繼續問，也可以按 retryJob 馬上重問 */
    jobError: latest.isError ? latest.error : null,
    jobLoading: latest.isFetching,
    retryJob: () => { void latest.refetch() },
    forThisVersion,
    confirmed,
    hasResult,
    complete,
    results,
    latestResultVersion: results.data?.[0]?.result_version ?? null,
    team: team.data ?? null,
    canSubmit: complete && !running && !confirmed && !(forThisVersion && job?.status === 'succeeded'),
    canRegenerate: complete && !running,
    submitting: submit.isPending,
    cancelling: cancel.isPending,
    submit: (regenerate: boolean) => submit.mutate(regenerate),
    cancel: () => cancel.mutate(),
    submitError: submit.isError ? submit.error : null,
    cancelError: cancel.isError ? cancel.error : null,
  }
}

export type Generation = ReturnType<typeof useGeneration>
