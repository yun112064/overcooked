import { Alert, Box, Button, LinearProgress, Stack, Typography } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api, errorMessage } from '../api/client'
import type { Job, MealRequest } from '../api/types'
import MealResultView from './MealResultView'

const STAGE_LABELS: Record<string, string> = {
  queued: '排隊中',
  started: '開始處理',
  validating_input: '檢查問卷',
  retrieving: '查詢資料庫食譜',
  ai_generating: 'AI 生成菜單中',
  validating: '檢查菜單條件',
  calculating: '計算營養與價格',
  knowledge: '查詢營養知識',
  explaining: '整理知識說明',
  retry_scheduled: '稍後自動重試',
  recovered: '重新排入佇列',
  result_ready: '已完成',
  failed: '失敗',
  needs_manual_retry: '需要確認後重新送出',
}

const ACTIVE = new Set(['queued', 'running'])

type Props = { row: MealRequest; onChanged: () => void }

/** 送出生成並追蹤工作狀態；有結果時顯示結果頁（T15）。查無資料庫菜單會自動改由 AI 生成，不詢問同意。 */
export default function GenerationPanel({ row, onChanged }: Props) {
  const queryClient = useQueryClient()
  // 同一問卷版本固定使用同一把冪等鍵：網路重送或重複點擊不會建立第二個工作
  const keys = useRef(new Map<number, string>())
  const keyFor = (version: number) => {
    if (!keys.current.has(version)) keys.current.set(version, crypto.randomUUID())
    return keys.current.get(version)!
  }

  const latest = useQuery({
    queryKey: ['jobs', row.id],
    queryFn: async () =>
      (await api.get<{ jobs: Job[] }>('/jobs', { params: { meal_request_id: row.id } })).data.jobs[0] ?? null,
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 2000 : false),
  })

  const submit = useMutation({
    mutationFn: async (regenerate: boolean = false) => {
      // 重新生成是新的命令：換一把冪等鍵，產生新的結果版本
      if (regenerate) keys.current.delete(row.version)
      return (await api.post<{ job: Job }>(`/meal-requests/${row.id}/submit`, { version: row.version },
        { headers: { 'Idempotency-Key': keyFor(row.version) } })).data.job
    },
    onSuccess: async (job) => {
      if (job.status === 'failed') keys.current.delete(row.version)
      queryClient.setQueryData(['jobs', row.id], job)
      onChanged()
    },
  })
  const job = latest.data
  const active = job && ACTIVE.has(job.status)

  // 工作由進行中變成結束時，請頁面重新讀取問卷狀態（解除生成中鎖定）
  const wasActive = useRef(false)
  useEffect(() => {
    if (wasActive.current && !active) {
      // 失敗後重新送出是新的請求，要換一把冪等鍵；否則只會拿回同一個失敗工作
      if (job?.status === 'failed' && job.input_version !== null) keys.current.delete(job.input_version)
      onChanged()
    }
    wasActive.current = !!active
  }, [active, job, onChanged])
  const lastJobForThisVersion = job && job.input_version === row.version
  const hasResult = row.status === 'result_ready' || row.status === 'awaiting_decision'
  const canSubmit = row.complete && !active && !(lastJobForThisVersion && job.status === 'succeeded')

  return (
    <Stack spacing={1.5}>
      {job && lastJobForThisVersion && (
        <Box>
          <Typography>
            生成狀態：{STAGE_LABELS[job.stage ?? ''] ?? job.status}（第 {job.attempts}／{job.max_attempts} 次嘗試）
          </Typography>
          {active && <LinearProgress sx={{ mt: 1 }} />}
          {job.status === 'failed' && job.error && (
            <Alert severity="error" sx={{ mt: 1 }}>
              {job.error.message}
            </Alert>
          )}
          {job.status === 'succeeded' && (
            <Alert severity="success" sx={{ mt: 1 }}>
              已產生結果{job.result_summary ? `（${job.result_summary.mode_label}，第 ${job.result_summary.result_version} 版）` : ''}。
              {row.status === 'awaiting_decision' && ' 有缺少的價格或營養資料，請在下方決定如何處理。'}
            </Alert>
          )}
        </Box>
      )}
      {submit.isError && <Alert severity="error">{errorMessage(submit.error)}</Alert>}
      {hasResult && !active && <MealResultView requestId={row.id} onChanged={onChanged} />}
      <Box>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          <Button variant="contained" disabled={!canSubmit || submit.isPending} onClick={() => submit.mutate(false)}>
            {job && lastJobForThisVersion && job.status === 'failed' ? '重新送出生成' : '送出生成'}
          </Button>
          {hasResult && (
            <Button variant="outlined" disabled={!row.complete || !!active || submit.isPending}
              onClick={() => submit.mutate(true)}>
              重新生成
            </Button>
          )}
        </Box>
        {!row.complete && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>補齊上方項目後才能送出。</Typography>
        )}
      </Box>
    </Stack>
  )
}
