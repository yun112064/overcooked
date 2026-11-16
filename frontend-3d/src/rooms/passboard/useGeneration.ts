/**
 * 送出生成、追蹤工作、取消（行為和舊前端的 GenerationPanel 相同）。
 *
 * - 工作狀態：GET /jobs?meal_request_id=&kind=meal_generation，進行中每 2 秒問一次。
 * - 冪等鍵：同一版問卷固定用同一把，重複點擊或網路重送不會建立第二個工作；
 *   重新生成、失敗後重送、取消後重送都是新的命令，要換一把。
 * - 工作從進行中變成結束時呼叫 onSettled（重新讀問卷、決定鏡頭要去哪）。
 * - 另外兩份別的房間在用的快取要跟著過期：廚房與客廳的工作清單（['jobs']，整個家的）、
 *   這份問卷的結果版本清單（['meal-results', id]）。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useEffectEvent, useRef } from 'react'
import { api } from '../../api/client'
import type { Job, MealRequest } from '../../api/types'
import { isActive, pollInterval } from './generation'
import { newKey } from './idempotency'

type Options = {
  row: Pick<MealRequest, 'id' | 'version' | 'status'>
  /** 送出、取消成功後：重新讀取問卷狀態 */
  onChanged: () => void
  /** 工作由進行中變成結束（成功、失敗或取消）；job 是結束時的工作 */
  onSettled: (job: Job | null) => void
}

export function useGeneration({ row, onChanged, onSettled }: Options) {
  const queryClient = useQueryClient()
  const keys = useRef(new Map<number, string>())
  const jobsKey = ['jobs', row.id]
  // exact：只讓廚房的清單過期；['jobs', id]（這裡自己的）剛寫進最新的工作，不用再問一次
  const touchOtherRooms = () => {
    void queryClient.invalidateQueries({ queryKey: ['jobs'], exact: true })
    void queryClient.invalidateQueries({ queryKey: ['meal-results', row.id] })
  }

  // 問卷本身在排隊／生成中：就算工作一次都還沒讀到（第一次讀取失敗）也要繼續問
  const requestLocked = row.status === 'queued' || row.status === 'running'
  const latest = useQuery({
    queryKey: jobsKey,
    queryFn: async () =>
      // 只看生成工作；結果重試（知識說明、定向查價）由餐桌自己追蹤
      (await api.get<{ jobs: Job[] }>('/jobs', { params: { meal_request_id: row.id, kind: 'meal_generation' } }))
        .data.jobs[0] ?? null,
    refetchInterval: (query) => pollInterval(query.state.data, requestLocked),
  })

  const submit = useMutation({
    mutationFn: async (regenerate: boolean) => {
      // 重新生成是新的命令：換一把冪等鍵，產生新的結果版本
      if (regenerate) keys.current.delete(row.version)
      const key = keys.current.get(row.version) ?? newKey()
      keys.current.set(row.version, key)
      return (await api.post<{ job: Job }>(`/meal-requests/${row.id}/submit`, { version: row.version },
        { headers: { 'Idempotency-Key': key } })).data.job
    },
    onSuccess: (job) => {
      if (job.status === 'failed') keys.current.delete(row.version)
      queryClient.setQueryData(jobsKey, job)
      touchOtherRooms()
      onChanged()
    },
  })

  const cancel = useMutation({
    mutationFn: async () => (await api.post<{ job: Job }>(`/meal-requests/${row.id}/cancel`)).data.job,
    onSuccess: (job) => {
      // 取消後重新送出是新的請求，要換一把冪等鍵
      if (job.input_version !== null) keys.current.delete(job.input_version)
      queryClient.setQueryData(jobsKey, job)
      touchOtherRooms()
      onChanged()
    },
  })

  const job = latest.data ?? null
  const active = isActive(job)

  // 工作由進行中變成結束：解除生成中的鎖定，並告訴場景結果
  const settled = useEffectEvent(() => {
    // 失敗後重新送出是新的請求，要換一把冪等鍵；否則只會拿回同一個失敗工作
    if (job?.status === 'failed' && job.input_version !== null) keys.current.delete(job.input_version)
    touchOtherRooms()
    onSettled(job)
  })
  const wasActive = useRef(false)
  useEffect(() => {
    if (wasActive.current && !active) settled()
    wasActive.current = active
  }, [active])

  return { latest, job, active, submit, cancel }
}

export type Generation = ReturnType<typeof useGeneration>
