import HistoryEduOutlined from '@mui/icons-material/HistoryEduOutlined'
import {
  Alert, Box, Chip, CircularProgress, Link, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'
import {
  Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, errorMessage } from '../api/client'
import { MEAL_LABELS, type MealPlanDetail } from '../api/types'
import { DishIngredients, DishSteps, HighSodiumTag } from '../components/DishDetail'
import PersonNutrition, { MacroBar } from '../components/PersonNutrition'
import { EmptyState, IconTile, PageHeader, Section, Stat, StatRow } from '../components/ui'
import { MEAL_VISUALS } from '../components/visuals'
import { tokens } from '../theme'

const money = (v: string | null | undefined) => (v === null || v === undefined ? '未知' : `${Math.round(Number(v))} 元`)

function isoDay(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const today = () => isoDay(new Date())

type TrendItem = {
  meal_date: string; meal_type: string
  energy_kcal: string | null; protein_g: string | null
  target_energy_kcal: string | null; target_protein_g: string | null
  cost_total: string | null; known_cost_total: string | null; budget: string | null
}
type DayPoint = {
  date: string; label: string; meals: number
  energy: number | null; target: number | null; cost: number | null; budget: number | null
  unknownEnergy: boolean; unknownCost: boolean
}

/** 逐日加總；有任何一餐未知就標記，該日數值只算已知部分。沒有紀錄的日子留空，讓時間軸連續。 */
function byDay(items: TrendItem[], since: string, days: number): DayPoint[] {
  const start = new Date(`${since}T00:00:00`)
  const points = new Map<string, DayPoint>()
  for (let i = 0; i < days; i++) {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    const date = isoDay(d)
    points.set(date, { date, label: `${d.getMonth() + 1}/${d.getDate()}`, meals: 0, energy: null, target: null,
      cost: null, budget: null, unknownEnergy: false, unknownCost: false })
  }
  const add = (a: number | null, v: string | null) => (v === null ? a : (a ?? 0) + Number(v))
  for (const it of items) {
    const p = points.get(it.meal_date)
    if (!p) continue
    p.meals += 1
    p.energy = add(p.energy, it.energy_kcal)
    p.target = add(p.target, it.target_energy_kcal)
    // 價格不完整時以已知部分畫圖，並標記 unknownCost
    p.cost = add(p.cost, it.cost_total ?? it.known_cost_total)
    p.budget = add(p.budget, it.budget)
    p.unknownEnergy ||= it.energy_kcal === null
    p.unknownCost ||= it.cost_total === null
  }
  return [...points.values()]
}

const axis = { fontSize: 11, fill: tokens.inkMuted }

function ChartTooltip({ active, payload, kind }: {
  active?: boolean; payload?: { payload: DayPoint }[]; kind: 'energy' | 'cost'
}) {
  const p = payload?.[0]?.payload
  if (!active || !p || p.meals === 0) return null
  const energy = kind === 'energy'
  const value = energy ? p.energy : p.cost
  const ref = energy ? p.target : p.budget
  const unknown = energy ? p.unknownEnergy : p.unknownCost
  return (
    <Box sx={{ bgcolor: tokens.paper, border: `1px solid ${tokens.line}`, px: 1.5, py: 1, borderRadius: 1,
      boxShadow: '0 6px 18px rgba(63,58,52,0.08)' }}>
      <Typography variant="caption" component="p" sx={{ color: tokens.inkMuted }}>{p.date}・{p.meals} 餐</Typography>
      <Typography variant="body2">
        {energy ? '熱量' : '成本'} {value === null ? '未知' : `${Math.round(value)} ${energy ? 'kcal' : '元'}`}
        {unknown && value !== null && '（僅已知部分）'}
      </Typography>
      {ref !== null && (
        <Typography variant="caption" component="p">{energy ? '目標' : '預算'} {Math.round(ref)} {energy ? 'kcal' : '元'}</Typography>
      )}
      <Typography variant="caption" component="p" sx={{ color: tokens.indigo }}>點一下查看當天紀錄</Typography>
    </Box>
  )
}

function TrendChart({ data, kind, selected, onPick }: {
  data: DayPoint[]; kind: 'energy' | 'cost'; selected: string; onPick: (date: string) => void
}) {
  const energy = kind === 'energy'
  const barColor = energy ? tokens.indigo : tokens.matcha
  return (
    <Box sx={{ height: 220, mx: -1 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
          onClick={(e) => { const d = e?.activeLabel !== undefined ? data.find((p) => p.label === e.activeLabel) : undefined; if (d?.meals) onPick(d.date) }}>
          <CartesianGrid vertical={false} stroke={tokens.line} />
          <XAxis dataKey="label" tick={axis} tickLine={false} axisLine={{ stroke: tokens.line }}
            interval="preserveStartEnd" minTickGap={16} />
          <YAxis tick={axis} tickLine={false} axisLine={false} width={44}
            domain={[0, (max: number) => Math.ceil((max * 1.15) / 10) * 10]} allowDecimals={false} />
          <Tooltip cursor={{ fill: 'rgba(63,58,52,0.04)' }} content={<ChartTooltip kind={kind} />} />
          <Bar dataKey={energy ? 'energy' : 'cost'} name={energy ? '熱量' : '成本'} maxBarSize={22} radius={[2, 2, 0, 0]}
            fill={barColor} fillOpacity={0.8} style={{ cursor: 'pointer' }} isAnimationActive={false}
            shape={(props: { x?: number; y?: number; width?: number; height?: number; payload?: DayPoint }) => {
              const partial = energy ? props.payload?.unknownEnergy : props.payload?.unknownCost
              return (
                <rect x={props.x} y={props.y} width={props.width} height={Math.max(0, props.height ?? 0)} rx={2}
                  fill={barColor} fillOpacity={(props.payload?.date === selected ? 1 : 0.55) * (partial ? 0.45 : 1)}
                  stroke={partial ? barColor : 'none'} strokeDasharray={partial ? '3 2' : undefined} />
              )
            }} />
          {/* 目標／預算畫成每天一條短刻度，不連線：沒紀錄的日子沒有目標可比 */}
          <Line dataKey={energy ? 'target' : 'budget'} name={energy ? '目標' : '預算'} stroke="none"
            isAnimationActive={false} activeDot={false}
            dot={(props: { cx?: number; cy?: number; index?: number; value?: number | null }) => (
              props.value === null || props.value === undefined || props.cy === undefined || props.cx === undefined
                ? <g key={props.index} />
                : <line key={props.index} x1={props.cx - 9} x2={props.cx + 9} y1={props.cy} y2={props.cy}
                    stroke={energy ? tokens.brick : tokens.ochre} strokeWidth={2} />
            )} />
        </ComposedChart>
      </ResponsiveContainer>
    </Box>
  )
}

function Legend({ items }: { items: [string, string, boolean][] }) {
  return (
    <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap' }}>
      {items.map(([label, color, dashed]) => (
        <Box key={label} sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
          <Box sx={dashed
            ? { width: 14, borderTop: `2px solid ${color}` }
            : { width: 10, height: 10, bgcolor: color, opacity: 0.8, borderRadius: '2px' }} />
          <Typography variant="caption">{label}</Typography>
        </Box>
      ))}
    </Box>
  )
}

function Trends({ selected, onPick }: { selected: string; onPick: (date: string) => void }) {
  const [days, setDays] = useState(30)
  const trend = useQuery({
    queryKey: ['meal-plan-trend', days, today()],
    // 截止日用本機今天：預先安排的未來餐食不列入統計，與圖表範圍一致
    queryFn: async () => (await api.get<{ since: string; until: string; items: TrendItem[] }>('/meal-plans/trend',
      { params: { days, until: today() } })).data,
  })
  const data = useMemo(() => (trend.data ? byDay(trend.data.items, trend.data.since, days) : []), [trend.data, days])
  const items = trend.data?.items ?? []
  const withEnergy = items.filter((i) => i.energy_kcal !== null && i.target_energy_kcal)
  const reach = withEnergy.length
    ? withEnergy.reduce((s, i) => s + Number(i.energy_kcal) / Number(i.target_energy_kcal), 0) / withEnergy.length
    : null
  const costs = items.filter((i) => i.cost_total !== null)
  const partialCosts = items.filter((i) => i.cost_total === null && i.known_cost_total !== null).length
  const totalCost = costs.reduce((s, i) => s + Number(i.cost_total), 0)
  const underBudget = costs.filter((i) => i.budget !== null && Number(i.cost_total) <= Number(i.budget)).length

  return (
    <Section title="趨勢" aside={
      <ToggleButtonGroup size="small" exclusive value={days} onChange={(_, v) => v && setDays(v)}
        sx={{ '& .MuiToggleButton-root': { px: 1.5, py: 0.25, letterSpacing: '0.08em', borderColor: tokens.line } }}>
        <ToggleButton value={7}>7 天</ToggleButton>
        <ToggleButton value={30}>30 天</ToggleButton>
        <ToggleButton value={90}>90 天</ToggleButton>
      </ToggleButtonGroup>
    }>
      {trend.isPending && <CircularProgress size={24} />}
      {trend.isError && <Alert severity="error">{errorMessage(trend.error)}</Alert>}
      {trend.data && (
        <Stack spacing={4}>
          <StatRow>
            <Stat label="採用餐數" value={items.length} unit="餐" note={`近 ${days} 天`} />
            <Stat label="熱量達成" value={reach === null ? '—' : Math.round(reach * 100)} unit={reach === null ? undefined : '%'}
              tone={reach === null ? 'default' : Math.abs(reach - 1) <= 0.1 ? 'good' : 'warn'}
              note={reach === null ? '沒有可比較的紀錄' : Math.abs(reach - 1) <= 0.1 ? '平均落在目標 ±10% 內' : '平均偏離目標超過 10%'} />
            <Stat label="平均每餐成本" value={costs.length ? Math.round(totalCost / costs.length) : '—'} unit={costs.length ? '元' : undefined}
              note={costs.length < items.length ? `${items.length - costs.length} 餐價格未知` : undefined} />
            <Stat label="預算內" value={costs.length ? `${underBudget}/${costs.length}` : '—'} unit={costs.length ? '餐' : undefined}
              tone={costs.length && underBudget === costs.length ? 'good' : 'default'} />
          </StatRow>
          {items.length === 0 ? (
            <Typography variant="body2" color="text.secondary">近 {days} 天沒有已採用的餐食，採用菜單後這裡會出現趨勢。</Typography>
          ) : (
            <>
              <Box>
                <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2, flexWrap: 'wrap', mb: 1 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500, flexGrow: 1 }}>每日熱量（kcal）</Typography>
                  <Legend items={[['實際', tokens.indigo, false], ['目標', tokens.brick, true]]} />
                </Box>
                <TrendChart data={data} kind="energy" selected={selected} onPick={onPick} />
              </Box>
              <Box>
                <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2, flexWrap: 'wrap', mb: 1 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500, flexGrow: 1 }}>每日成本（元）</Typography>
                  <Legend items={[['成本', tokens.matcha, false], ['預算', tokens.ochre, true]]} />
                  {partialCosts > 0 && <Typography variant="caption" sx={{ width: '100%' }}>虛線框的長條是價格不完整、只計已知部分的日子</Typography>}
                </Box>
                <TrendChart data={data} kind="cost" selected={selected} onPick={onPick} />
              </Box>
            </>
          )}
        </Stack>
      )}
    </Section>
  )
}

/** 單筆已採用餐食：顯示的都是採用當下保存的快照，來源之後更新不會改變。 */
function PlanCard({ plan }: { plan: MealPlanDetail }) {
  const prices = plan.prices?.status === 'computed' ? plan.prices : null
  return (
    <Section title={
      <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 1.5 }}>
        {MEAL_VISUALS[plan.meal_type] && <IconTile icon={MEAL_VISUALS[plan.meal_type].icon} tone={MEAL_VISUALS[plan.meal_type].tone} size={34} />}
        {MEAL_LABELS[plan.meal_type]}
      </Box>
    } aside={`採用於 ${new Date(plan.adopted_at).toLocaleString()}`}>
      <Stack spacing={2.5}>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
          <Chip size="small" color={plan.ai_generated ? 'secondary' : 'default'} variant={plan.ai_generated ? 'outlined' : 'filled'}
            label={plan.mode_label} />
          {plan.price_exception_accepted && <Chip size="small" color="warning" variant="outlined" label="部分價格未知" />}
          {plan.result_version !== null && <Chip size="small" label={`結果第 ${plan.result_version} 版`} />}
        </Box>
        {plan.fallback_message && <Alert severity="info">{plan.fallback_message}</Alert>}
        {prices && (
          <StatRow>
            <Stat label="成本價" value={prices.cost_total !== null ? Math.round(Number(prices.cost_total)) : '未知'}
              unit={prices.cost_total !== null ? '元' : undefined}
              note={prices.cost_total === null ? `已知 ${money(prices.known_cost_total)}` : undefined} />
            <Stat label="採購價" value={prices.purchase_total !== null ? Math.round(Number(prices.purchase_total)) : '未知'}
              unit={prices.purchase_total !== null ? '元' : undefined}
              note={prices.purchase_total === null ? `已知 ${money(prices.known_purchase_total)}` : undefined} />
            <Stat label="預算" value={prices.budget !== null ? Math.round(Number(prices.budget)) : '—'} unit={prices.budget !== null ? '元' : undefined} />
            <Stat label="菜數" value={plan.dishes.length} unit="道" />
          </StatRow>
        )}
        {plan.nutrition && <MacroBar nutrition={plan.nutrition} />}
        <Stack spacing={2} divider={<Box sx={{ borderTop: `1px solid ${tokens.line}` }} />}>
          {plan.dishes.map((d) => (
            <Box key={d.standard_dish_id}>
              <Typography component="div" sx={{ fontFamily: tokens.serif, fontSize: '1.05rem', letterSpacing: '0.06em', display: 'flex', alignItems: 'baseline', flexWrap: 'wrap' }}>
                {d.dish_name}
                {d.cooking_method && <Typography component="span" variant="caption" sx={{ ml: 1 }}>{d.cooking_method}</Typography>}
                {d.aliases.length > 0 && <Typography component="span" variant="caption" sx={{ ml: 1 }}>又名 {d.aliases.join('、')}</Typography>}
                <Box component="span" sx={{ ml: 1 }}><HighSodiumTag high={d.high_sodium} sodium={d.sodium_mg_per_serving} /></Box>
              </Typography>
              {d.ingredients && <DishIngredients ingredients={d.ingredients} />}
              {d.steps && <DishSteps steps={d.steps} display={d.steps_display} />}
              {d.recipe_source && (
                <Typography variant="caption">
                  食譜來源：<Link href={d.recipe_source.source_url} target="_blank" rel="noreferrer">{d.recipe_source.source_name}</Link>
                </Typography>
              )}
            </Box>
          ))}
        </Stack>
        {/* 決策 #53：與結果頁相同的 7 種營養素與未知顯示（以 null 判斷，0 照常顯示） */}
        {plan.nutrition && (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 500, mb: 1.5 }}>每人營養（這一餐／每日建議）</Typography>
            <PersonNutrition nutrition={plan.nutrition} />
          </Box>
        )}
        {plan.previous_day && (
          <Typography variant="body2" color="text.secondary">
            昨日比對（{plan.previous_day.previous_date}）：
            {plan.previous_day.excluded_dishes.length > 0
              ? `排除 ${plan.previous_day.excluded_dishes.map((d) => d.dish_name).join('、')}`
              : '沒有排除的菜'}
            {plan.previous_day.specified_repeats.length > 0 && `；指定保留 ${plan.previous_day.specified_repeats.join('、')}`}
          </Typography>
        )}
        {plan.knowledge?.status === 'available' && plan.knowledge.items.length > 0 && (
          <Box sx={{ bgcolor: tokens.washi, borderRadius: 1, p: 2 }}>
            <Typography variant="caption" component="p" sx={{ letterSpacing: '0.16em', mb: 1 }}>食材小知識</Typography>
            <Stack spacing={1}>
              {plan.knowledge.items.map((k) => (
                <Typography key={k.ingredient_id} variant="body2">
                  <Box component="span" sx={{ fontWeight: 500, mr: 1 }}>{k.ingredient_name ?? k.ingredient_id}</Box>{k.explanation}
                  <Typography component="span" variant="caption">
                    （{k.citations.map((c) => (c.attribution
                      ? `${c.attribution} ${c.source_url ?? ''}`.trim()
                      : `來源：${c.knowledge_id} ${c.source_version}`)).join('、')}）
                  </Typography>
                </Typography>
              ))}
            </Stack>
          </Box>
        )}
      </Stack>
    </Section>
  )
}

/** T16 已採用餐食紀錄：上方趨勢圖、下方依日期回看當日各餐別的完整快照。只有按「採用」的餐食會出現在這裡。 */
export default function MealHistoryPage() {
  const [params, setParams] = useSearchParams()
  const date = params.get('date') || today()
  const pick = (d: string) => setParams({ date: d })

  const dates = useQuery({
    queryKey: ['meal-plan-dates'],
    queryFn: async () => (await api.get<{ dates: { meal_date: string; count: number }[] }>('/meal-plans')).data.dates,
  })
  const plans = useQuery({
    queryKey: ['meal-plans', date],
    queryFn: async () =>
      (await api.get<{ meal_plans: MealPlanDetail[] }>('/meal-plans', { params: { date } })).data.meal_plans,
  })

  return (
    <Stack spacing={4}>
      <PageHeader eyebrow="History" title="已採用紀錄"
        description="只列出按「採用」保存的餐食，不代表實際已食用；僅生成或查看的菜單不會出現。" />
      {dates.data && dates.data.length === 0 ? (
        <EmptyState icon={<HistoryEduOutlined />} title="還沒有採用過菜單"
          description="在規劃餐食頁產生菜單並按「採用」後，這裡會記錄每一餐，並畫出熱量與成本的趨勢。" />
      ) : (
        <>
          <Trends selected={date} onPick={pick} />
          <Stack spacing={2}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
              <Typography variant="h6" component="h2" sx={{ flexGrow: 1 }}>{date} 的餐食</Typography>
              <TextField type="date" size="small" label="日期" value={date} sx={{ width: 180 }}
                onChange={(e) => e.target.value && pick(e.target.value)}
                slotProps={{ inputLabel: { shrink: true } }} />
            </Box>
            {dates.data && dates.data.length > 0 && (
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                {dates.data.slice(0, 14).map((d) => (
                  <Chip key={d.meal_date} size="small" label={`${d.meal_date.slice(5).replace('-', '/')}・${d.count}`} clickable
                    color={d.meal_date === date ? 'primary' : 'default'} variant={d.meal_date === date ? 'filled' : 'outlined'}
                    onClick={() => pick(d.meal_date)} />
                ))}
              </Box>
            )}
            {plans.isError && <Alert severity="error">{errorMessage(plans.error)}</Alert>}
            {plans.isPending && <CircularProgress size={24} />}
            {plans.data && plans.data.length === 0 && (
              <Typography variant="body2" color="text.secondary">{date} 沒有已採用餐食紀錄。</Typography>
            )}
            <Stack spacing={2} className="rise-in">{plans.data?.map((p) => <PlanCard key={p.id} plan={p} />)}</Stack>
          </Stack>
        </>
      )}
    </Stack>
  )
}
