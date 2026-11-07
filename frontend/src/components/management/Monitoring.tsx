import { useState } from 'react'
import { Alert, Button, Chip, Paper, Stack, TextField, Typography } from '@mui/material'
import { connectionStates, taskStates, time } from '../../api/management'
import type { Collection, Connection, Metrics, Observation, Task, DataType } from '../../api/management'
import { ReadState, ResultPage } from './shared'
import { useManagementRead } from './hooks'

const statuses = {
  historical_requires_refresh: '歷史觀察，需重新取得',
  live_observation: '來源觀察',
  unusable: '資料不可用',
}

export type PriceLookup = { recipe_release_id: string; food_id: string; as_of: string }

export function PriceObservations({ seed, draft }: {
  seed?: PriceLookup
  draft?: (value: PriceLookup, observation: Observation) => void
}) {
  const [release, setRelease] = useState(seed?.recipe_release_id ?? '')
  const [food, setFood] = useState(seed?.food_id ?? '')
  const [filters, setFilters] = useState<PriceLookup | undefined>(seed)
  const [cursor, setCursor] = useState<string>()
  const records = useManagementRead<Collection<Observation>>('/price-observations', filters ? { ...filters, cursor } : undefined, Boolean(filters))
  return <Stack spacing={2}>
    <Alert severity="info">原始觀察保留來源查詢時間。人工價格另走修訂與審核，已發布與已啟用分開顯示。</Alert>
    <Stack component="form" spacing={1} onSubmit={(event) => {
      event.preventDefault()
      if (!release.trim() || !food.trim()) return
      setCursor(undefined)
      setFilters({ recipe_release_id: release.trim(), food_id: food.trim(), as_of: new Date().toISOString() })
    }}>
      <TextField label="食譜資料版本識別碼" value={release} required size="small" onChange={(event) => setRelease(event.target.value)} />
      <TextField label="Food ID" value={food} required size="small" onChange={(event) => setFood(event.target.value)} />
      <Button variant="outlined" type="submit" sx={{ alignSelf: 'start' }}>查詢原始價格觀察</Button>
    </Stack>
    {filters && <>
      <Typography color="text.secondary">查詢基準時間 {time(filters.as_of)}（台北）</Typography>
      <ReadState query={records} unavailable="原始價格查詢目前不可用，或查詢指定的版本與食材不存在。">{(data) =>
        <ResultPage empty={!data.items.length} next={data.next_cursor} advance={setCursor} reset={cursor ? () => setCursor(undefined) : undefined}>
          {data.items.map((item) => <Paper key={item.snapshot_id} variant="outlined" sx={{ p: 1.5 }}>
            <Stack spacing={1}>
              <Typography>原始價格 {item.raw_price_twd === null ? '未知' : 'NT$ ' + item.raw_price_twd}</Typography>
              <Typography variant="body2">{item.provider} · 商品 {item.product_id ?? '未知'} · 觀察編號 {item.snapshot_id}</Typography>
              <Typography variant="body2">查詢時間 {time(item.queried_at)} · 有效迄日 {time(item.effective_valid_until)}</Typography>
              <Typography variant="body2">{statuses[item.usage_status]} · {item.calculation_usable ? '符合來源觀察品質檢查' : '未達計算資料要求'}
                {item.missing_reason && ' · ' + item.missing_reason}</Typography>
              {draft && ['pxmart', 'uniprosperity', 'manual_evidence'].includes(item.provider) &&
                <Button onClick={() => draft(filters, item)} sx={{ alignSelf: 'start' }}>依此觀察建立人工草稿</Button>}
            </Stack>
          </Paper>)}
        </ResultPage>
      }</ReadState>
    </>}
  </Stack>
}

export function Operations() {
  const [cursor, setCursor] = useState<string>()
  const metrics = useManagementRead<Metrics>('/metrics/summary', undefined, true, 15_000)
  const tasks = useManagementRead<Collection<Task>>('/tasks', { cursor }, true, 10_000)
  const connections = useManagementRead<Collection<Connection>>('/connections', undefined, true, 20_000)
  return <Stack spacing={2}>
    <Alert severity="info">監控僅涵蓋目前管理權限內的資料。連線探測尚未設定，外部執行停用。</Alert>
    <Typography variant="h6">管理摘要</Typography>
    <ReadState query={metrics}>{(data) => <Stack spacing={0.5}>
      <Typography>待審提案 {data.pending_proposal_count ?? '未知'} · 執行中任務 {data.running_task_count ?? '未知'}</Typography>
      <Typography>食譜數 {data.recipe_count ?? '未知'} · 資料庫容量 {data.database_bytes === null ? '未知' : data.database_bytes + ' bytes'}</Typography>
      <Typography variant="body2" color="text.secondary">觀察時間 {time(data.as_of)}</Typography>
    </Stack>}</ReadState>
    <Typography variant="h6">任務</Typography>
    <ReadState query={tasks}>{(data) => <ResultPage empty={!data.items.length} next={data.next_cursor} advance={setCursor}
      reset={cursor ? () => setCursor(undefined) : undefined}>
      {data.items.map((item) => <Paper key={item.task_id} variant="outlined" sx={{ p: 1.5 }}>
        <Stack spacing={0.5}>
          <Typography>{item.kind === 'proposal_validation' ? '提案驗證' : item.kind} · {taskStates[item.status] ?? item.status}</Typography>
          <Typography variant="body2" sx={{ overflowWrap: 'anywhere' }}>{item.task_id} · 嘗試 {item.attempt} 次 · 觀察時間 {time(item.observed_at)}</Typography>
        </Stack>
      </Paper>)}
    </ResultPage>}</ReadState>
    <Typography variant="h6">連線</Typography>
    <ReadState query={connections}>{(data) => data.items.length ? <Stack spacing={1.5}>
      {data.items.map((item) => <Paper key={item.registered_target_key} variant="outlined" sx={{ p: 1.5 }}>
        <Typography>{item.registered_target_key} · {connectionStates[item.status] ?? '未知'}</Typography>
        <Typography variant="body2">檢查時間 {time(item.checked_at)} · 延遲 {item.latency_ms === null ? '未知' : item.latency_ms + ' ms'}</Typography>
      </Paper>)}
    </Stack> : <Typography>沒有此權限範圍內的連線項目。</Typography>}</ReadState>
  </Stack>
}

const policy: Record<string, string> = {
  reviewed_publication: '需審核與發布', domain_command: '限指定操作', append_only: '僅追加', system_only: '由系統管理', no_direct_write: '不可直接修改',
}
const action: Record<string, string> = {
  metadata_only: '僅顯示中繼資料', scoped_read: '限範圍查閱', redacted_read: '遮罩後查閱', review_workflow: '依領域走審核', no_access: '內容不可查閱',
}

export function DataRegistry() {
  const [cursor, setCursor] = useState<string>()
  const registry = useManagementRead<Collection<DataType>>('/data-types', { cursor })
  return <Stack spacing={2}>
    <Alert severity="info">清冊列出資料種類、負責領域與政策。各項資料仍遵守原有可見範圍。</Alert>
    <ReadState query={registry}>{(data) => <ResultPage empty={!data.items.length} next={data.next_cursor} advance={setCursor}
      reset={cursor ? () => setCursor(undefined) : undefined}>
      {data.items.map((item) => <Paper key={item.key} variant="outlined" sx={{ p: 1.5 }}>
        <Typography sx={{ overflowWrap: 'anywhere' }}>{item.key} · {item.owner}</Typography>
        <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
          <Chip label={action[item.phase1_action] ?? item.phase1_action} size="small" />
          <Chip label={policy[item.write_policy] ?? item.write_policy} size="small" />
        </Stack>
      </Paper>)}
    </ResultPage>}</ReadState>
  </Stack>
}
