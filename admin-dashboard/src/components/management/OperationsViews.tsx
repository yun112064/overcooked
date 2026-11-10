import { useState } from 'react'
import { Alert, Button, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { domains, time } from '../../api/management'
import type { AuditEvent, Collection, DatabaseStatus, Domain, EditableDomain, OverviewSummary, ScheduleConfiguration } from '../../api/management'
import { useManagementRead } from './hooks'
import { ReadState, ResultPage } from './shared'

type QueueState = 'submitted' | 'changes_requested'
const queueStates = { submitted: '待審', changes_requested: '待補件' } as const

export function Overview({ openQueue, openOperations, openSchedules }: {
  openQueue: (domain: EditableDomain, state: QueueState) => void
  openOperations: () => void
  openSchedules: () => void
}) {
  const summary = useManagementRead<OverviewSummary>('/overview', undefined, true, 30_000)
  return <Stack spacing={2}>
    <Typography variant="h6">總覽</Typography>
    <ReadState query={summary}>{(data) => <Stack spacing={1}>
      {Object.entries(data.proposals).map(([domain, counts]) => <Paper key={domain} variant="outlined" sx={{ p: 1.5 }}>
        <Typography>{domains[domain as Domain]}</Typography>
        <Stack direction="row" spacing={1}>
          {(Object.keys(queueStates) as QueueState[]).map((state) => domain === 'source_policy'
            ? <Typography key={state} variant="body2">{queueStates[state]} {counts[state]}</Typography>
            : <Button key={state} onClick={() => openQueue(domain as EditableDomain, state)}>{queueStates[state]} {counts[state]}</Button>)}
        </Stack>
      </Paper>)}
      <Typography>失敗的提案驗證任務 {data.failed_validation_tasks ?? '未知'}</Typography>
      <Typography variant="body2" color="text.secondary">資料時間 {time(data.observed_at)}</Typography>
    </Stack>}</ReadState>
    <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
      <Button onClick={openOperations}>查看資料庫與任務</Button>
      <Button onClick={openSchedules}>查看排程設定</Button>
    </Stack>
  </Stack>
}

export function PrimaryDatabaseStatus() {
  const status = useManagementRead<DatabaseStatus>('/database-status', undefined, true, 30_000)
  return <Stack spacing={1}>
    <Typography variant="h6">主資料庫</Typography>
    <ReadState query={status}>{(data) => <>
      <Typography>{data.status === 'healthy' ? '查詢正常' : '監控未接入'}</Typography>
      <Typography variant="body2">探測查詢 {data.query_latency_ms === null ? '未知' : `${data.query_latency_ms} ms`}
        {' · '}目前連線 {data.client_connections ?? '未知'}
        {' · '}容量 {data.database_bytes === null ? '未知' : `${data.database_bytes.toLocaleString('zh-TW')} bytes`}</Typography>
      <Typography variant="body2" color="text.secondary">檢查時間 {time(data.checked_at)}</Typography>
    </>}</ReadState>
  </Stack>
}

const scheduleNames: Record<string, string> = {
  recover_expired_jobs: '回收逾時工作', expire_queued_jobs: '處理排隊逾時工作', log_cleanup: '日誌清理', price_collection: '每日查價',
}
const modes = { apply: '設定為實際清理', dry_run: '設定為預覽', enabled: '設定啟用', disabled: '設定停用', invalid: '設定有誤' }

type PriceRunHistory = {
  observed_at: string; status: 'available' | 'not_configured'
  items: { run_key: string; scheduled_for: string; started_at: string; finished_at: string | null; status: string
    duration_ms: number | null; ingredient_total: number | null; success_count: number | null
    failure_count: number | null; has_error: boolean }[]
}

const runStates: Record<string, string> = {
  running: '執行中', completed: '完成', failed: '失敗', paused: '暫停', partial: '部分完成',
}

function ScheduledPriceHistory() {
  const runs = useManagementRead<PriceRunHistory>('/schedule-price-runs', undefined, true, 30_000)
  return <Stack spacing={1}>
    <Typography variant="h6">每日查價執行紀錄</Typography>
    <ReadState query={runs}>{(data) => <>
      {data.status === 'not_configured' ? <Typography>執行紀錄來源未接入。</Typography> : <>
        {!data.items.length && <Typography>目前沒有查價排程紀錄。</Typography>}
        {data.items.map((run) => <Paper key={run.run_key} variant="outlined" sx={{ p: 1.5 }}>
          <Typography>{runStates[run.status] ?? run.status}{run.has_error && ' · 有執行錯誤'}</Typography>
          <Typography variant="body2">排定 {time(run.scheduled_for)} · 開始 {time(run.started_at)} · 結束 {time(run.finished_at)}</Typography>
          <Typography variant="body2">耗時 {run.duration_ms === null ? '未知' : `${run.duration_ms} ms`} · 食材 {run.ingredient_total ?? '未知'}
            {' · '}成功 {run.success_count ?? '未知'} · 失敗 {run.failure_count ?? '未知'}</Typography>
        </Paper>)}
      </>}
      <Typography variant="body2" color="text.secondary">資料時間 {time(data.observed_at)}</Typography>
    </>}</ReadState>
  </Stack>
}

export function Schedules() {
  const schedule = useManagementRead<ScheduleConfiguration>('/schedule-configuration', undefined, true, 30_000)
  return <Stack spacing={2}>
    <Typography variant="h6">排程中心</Typography>
    <Alert severity="info">排程設定來自目前 API 環境。排程程序的運作狀態及下次執行時間目前未知，執行紀錄僅涵蓋每日查價。</Alert>
    <ReadState query={schedule}>{(data) => <Stack spacing={1}>
      {data.items.map((item) => <Paper key={item.key} variant="outlined" sx={{ p: 1.5 }}>
        <Typography>{scheduleNames[item.key] ?? item.key}</Typography>
        <Typography variant="body2">{item.interval_seconds !== undefined ? `每 ${item.interval_seconds} 秒` :
          item.hour === undefined ? '排程時間未知' :
            `每日 ${String(item.hour).padStart(2, '0')}:${String(item.minute).padStart(2, '0')} (${item.timezone})`}
        {item.configured_mode && ` · ${modes[item.configured_mode]}`}</Typography>
      </Paper>)}
      <Typography variant="body2" color="text.secondary">設定讀取時間 {time(data.observed_at)}</Typography>
      {data.items.some((item) => item.key === 'price_collection') && <ScheduledPriceHistory />}
    </Stack>}</ReadState>
  </Stack>
}

const actions: Record<string, string> = {
  'artifact.upload': '上傳來源', 'proposal.create': '建立提案', 'proposal.revise': '修訂提案',
  'proposal.validate': '要求驗證', 'validation.completed': '完成驗證', 'proposal.submit': '送審',
  'proposal.review': '審查提案', 'proposal.withdraw': '撤回提案', 'publication.build': '建立發布版本',
  'binding.plan': '建立啟用計畫', 'binding.activate': '啟用版本', 'binding.rollback': '回復版本',
}

export function AuditHistory() {
  const [domain, setDomain] = useState<Domain | ''>('')
  const [cursor, setCursor] = useState<string>()
  const audit = useManagementRead<Collection<AuditEvent> & { domains: Domain[] }>('/audit-events', { domain: domain || undefined, cursor })
  return <Stack spacing={2}>
    <Typography variant="h6">操作紀錄</Typography>
    <TextField select size="small" label="紀錄領域" value={domain} onChange={(event) => {
      setDomain(event.target.value as Domain | '')
      setCursor(undefined)
    }}>
      <MenuItem value="">所有授權領域</MenuItem>
      {(audit.data?.domains ?? (domain ? [domain] : [])).map((value) => <MenuItem key={value} value={value}>{domains[value]}</MenuItem>)}
    </TextField>
    <ReadState query={audit}>{(data) => <ResultPage empty={!data.items.length} next={data.next_cursor} advance={setCursor}
      reset={cursor ? () => setCursor(undefined) : undefined}>
      {data.items.map((event) => <Paper key={event.event_id} variant="outlined" sx={{ p: 1.5, overflowWrap: 'anywhere' }}>
        <Typography>{domains[event.domain]} · {actions[event.action] ?? event.action} · {time(event.created_at)}</Typography>
        <Typography variant="body2">{event.reason}</Typography>
        <Typography variant="body2" color="text.secondary">操作者 {event.actor_id} · 資料 {event.resource_id}</Typography>
      </Paper>)}
    </ResultPage>}</ReadState>
  </Stack>
}
