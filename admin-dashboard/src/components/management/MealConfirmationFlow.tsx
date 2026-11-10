import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Box, Chip, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material'
import { errorStatus } from '../../api/client'
import { read, time } from '../../api/management'

const branches = [
  { stage: 'history_saved', label: '菜單歷史', event: '已保存採用紀錄' },
  { stage: 'ingredients_saved', label: '食材與用量', event: '已保存食材明細' },
  { stage: 'nutrition_saved', label: '營養摘要', event: '已保存營養摘要' },
  { stage: 'cost_saved', label: '成本與採買', event: '已保存成本結果' },
] as const

const stageLabels: Record<string, string> = {
  confirmed: '使用者確認菜單',
  snapshot_saved: '已保存菜單快照',
  ...Object.fromEntries(branches.map(({ stage, event }) => [stage, event])),
}

type FlowEvent = { sequence: number; stage: string; occurred_at: string }
type Trace = { trace_id: string; mode: 'database_menu' | 'ai_generated_menu'; events: FlowEvent[] }
type FlowFeed = { observed_at: string; items: Trace[] }
type Point = [number, number]

function traceNumber(id: string) {
  return id.slice(-4).toUpperCase()
}

function railGeometry(width: number) {
  const confirm: Point = [40, 43]
  const saved: Point = [width * .48, 43]
  const dispatch: Point = [width - 40, 43]
  const bend: Point = [dispatch[0], 64]
  const junction: Point = [width * .43, 64]
  const routes = branches.map((_, index) => {
    const y = 92 + index * 34
    const split: Point = [junction[0], y]
    const end: Point = [width - 34, y]
    return { split, end }
  })
  return { confirm, saved, dispatch, bend, junction, routes }
}

function points(values: Point[]) {
  return values.map((point) => point.join(',')).join(' ')
}

function translate([x, y]: Point) {
  return `translate(${x}px, ${y}px)`
}

function ConfirmationRail({ trace, live, follow }: { trace?: Trace; live: boolean; follow: boolean }) {
  const svg = useRef<SVGSVGElement>(null)
  const previous = useRef<{ id?: string; live: boolean } | undefined>(undefined)
  const [width, setWidth] = useState(320)
  const { confirm, saved, dispatch, bend, junction, routes } = railGeometry(width)
  const traceId = trace?.trace_id
  const number = traceId ? traceNumber(traceId) : ''
  const completed = new Set(trace?.events.map((event) => event.stage))

  useEffect(() => {
    const element = svg.current
    if (!element) return
    const observer = new ResizeObserver(() => setWidth(element.clientWidth))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const before = previous.current
    previous.current = { id: traceId, live }
    if (!live || !follow || !traceId || !before?.live || traceId === before.id ||
        window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const { confirm, saved, dispatch, bend, junction, routes } = railGeometry(width)
    const animations: Animation[] = []
    const lead = svg.current?.querySelector('[data-token="main"]')
    if (lead) animations.push(lead.animate([
      { transform: translate(confirm), opacity: 1 },
      { transform: translate(saved), opacity: 1 },
      { transform: translate(dispatch), opacity: 1, offset: .98 },
      { transform: translate(dispatch), opacity: 0 },
    ], { duration: 650 }))
    for (const [index, { split, end }] of routes.entries()) {
      const marker = svg.current?.querySelector(`[data-token="${index}"]`)
      if (!marker) continue
      animations.push(marker.animate(
        [dispatch, bend, junction, split, end].map((point) => ({ transform: translate(point) })),
        { delay: 650 + 85 * index, duration: 800, fill: 'backwards', easing: 'ease-in-out' },
      ))
    }
    return () => animations.forEach((animation) => animation.cancel())
  }, [follow, live, traceId, width])

  function token(label: string, position: Point, key: string | number, opacity = 1) {
    return <g data-token={key} opacity={opacity} style={{ transform: translate(position) }}>
      <rect x={-29} y={-9} width={58} height={18} rx={9} fill="#14724a" stroke="#a0dfbc" />
      <text x={0} y={0} fill="#fff" dominantBaseline="central" textAnchor="middle" fontSize={11} fontWeight={700}>{label}</text>
    </g>
  }

  return <Box component="svg" ref={svg} width="100%" height={215} role="img" aria-label="菜單確認及四條支線的位置"
    sx={{ display: 'block', bgcolor: '#172520', borderRadius: 2, overflow: 'hidden',
      backgroundImage: 'radial-gradient(#324943 0.65px, transparent 0.65px)', backgroundSize: '12px 12px' }}>
    <polyline points={points([confirm, saved, dispatch, bend, junction, routes[3].split])} fill="none"
      stroke={trace ? '#76caa1' : '#67837a'} strokeWidth={2} strokeLinejoin="round" />
    {[confirm, saved, dispatch].map(([x, y]) => <circle key={x} cx={x} cy={y} r={4} fill="#172520" stroke="#90a89e" />)}
    <g fill="#ecf2ed" fontSize={12}>
      <text x={confirm[0]} y={23} textAnchor="middle">使用者確認</text>
      <text x={saved[0]} y={23} textAnchor="middle">儲存快照</text>
      <text x={dispatch[0]} y={23} textAnchor="middle">資料分流</text>
    </g>
    {routes.map(({ split, end }, index) => {
      const { label, stage } = branches[index]
      const arrived = completed.has(stage)
      return <g key={stage}>
        <line x1={split[0]} y1={split[1]} x2={end[0]} y2={end[1]} stroke={arrived ? '#76caa1' : '#67837a'} strokeWidth={2} />
        <circle cx={end[0]} cy={end[1]} r={4} fill="#172520" stroke="#90a89e" />
        <text x={14} y={end[1] + 4} fill="#ecf2ed" fontSize={12}>{label}</text>
        {arrived && token(`${number}-${'ABCD'[index]}`, end, index)}
      </g>
    })}
    {trace && token(number, dispatch, 'main', 0)}
  </Box>
}

export function MealConfirmationFlow() {
  const [selected, setSelected] = useState('latest')
  const [connection, setConnection] = useState(() => ({ online: navigator.onLine, since: 0 }))
  useEffect(() => {
    const update = () => setConnection({ online: navigator.onLine, since: Date.now() })
    window.addEventListener('online', update)
    window.addEventListener('offline', update)
    return () => {
      window.removeEventListener('online', update)
      window.removeEventListener('offline', update)
    }
  }, [])

  const feed = useQuery({
    queryKey: ['management', '/meal-confirmation-flows'],
    queryFn: ({ signal }) => read<FlowFeed>('/meal-confirmation-flows', undefined, signal, 5000),
    enabled: connection.online,
    retry: false,
    refetchInterval: 2000,
  })
  const status = errorStatus(feed.error)
  if (status === 401 || status === 403) return <Alert severity="warning">目前帳號無法查看即時資料流。</Alert>

  const data = feed.data
  const live = Boolean(data && connection.online && !feed.isError && feed.dataUpdatedAt >= connection.since)
  const disconnected = !connection.online || feed.isError
  const follow = selected === 'latest'
  const trace = follow ? data?.items[0] : data?.items.find((item) => item.trace_id === selected)
  return <Paper variant="outlined" sx={{ p: { xs: 1.5, sm: 2.5 } }}>
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="h6" component="h2">即時資料流</Typography>
        <Chip role="status" size="small" label={live ? '連線正常' : disconnected ? '連線中斷' : '同步中'}
          color={live ? 'success' : disconnected ? 'warning' : 'default'} />
      </Stack>
      <Typography color="text.secondary" variant="body2">最後確認更新 {time(data?.observed_at)}</Typography>
      {disconnected && <Alert severity="warning">{data ? '已停在最後確認的位置，等待重新連線。' : '目前無法讀取後端事件。'}</Alert>}
      <TextField select label="菜單確認紀錄" size="small" value={selected}
        onChange={(event) => setSelected(event.target.value)} sx={{ width: { xs: '100%', sm: 310 } }}>
        <MenuItem value="latest">跟隨最新確認</MenuItem>
        {data?.items.map((item) => <MenuItem key={item.trace_id} value={item.trace_id}>
          {traceNumber(item.trace_id)} · {time(item.events[0]?.occurred_at)}
        </MenuItem>)}
        {!follow && !trace && <MenuItem value={selected} disabled>此紀錄已超出最近清單</MenuItem>}
      </TextField>
      <ConfirmationRail trace={trace} live={live} follow={follow} />
      {trace ? <>
        <Typography variant="body2" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>
          追蹤 ID {trace.trace_id} · {trace.mode === 'ai_generated_menu' ? 'AI 生成菜單' : '資料庫菜單'}
        </Typography>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 1 }}>
          {branches.map(({ stage, label }, index) => {
            const event = trace.events.find((item) => item.stage === stage)
            return <Paper variant="outlined" sx={{ p: 1.25 }} key={stage}>
              <Typography variant="body2">{'ABCD'[index]} · {label}</Typography>
              <Chip size="small" variant="outlined" color={event ? 'success' : 'default'}
                label={event ? '已保存' : '未收到事件'} sx={{ mt: 0.75 }} />
            </Paper>
          })}
        </Box>
        <Box component="details">
          <Box component="summary" sx={{ cursor: 'pointer' }}>查看後端事件</Box>
          <Stack component="ol" spacing={0.5} sx={{ pl: 3, mb: 0 }}>
            {trace.events.map((event) => <Typography component="li" variant="body2" key={event.sequence}>
              {stageLabels[event.stage] ?? event.stage} · {time(event.occurred_at)}
            </Typography>)}
          </Stack>
        </Box>
      </> : data && <Typography variant="body2" color="text.secondary">
        {follow ? '目前沒有已記錄的菜單確認事件。' : '這筆確認已超出最近紀錄。'}
      </Typography>}
    </Stack>
  </Paper>
}
