/** 已採用紀錄（版面取自 t11-t20-demo 的 MealHistoryPage）：上方熱量趨勢圖、下方依日期回看當日各餐別的完整快照。
 *  只有按「採用」的餐食會出現。本分支尚未整合 T13 價格，所以只有熱量趨勢，沒有成本。 */
import HistoryEduOutlined from '@mui/icons-material/HistoryEduOutlined'
import {
  Alert, Box, Button, Chip, CircularProgress, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import {
  Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { api, errorMessage } from '../api/client'
import { toNumber, withinTarget } from '../api/nutrition'
import { MEAL_LABELS, type MealPlanDetail } from '../api/types'
import DishList from '../components/DishList'
import PersonNutrition from '../components/PersonNutrition'
import { EmptyState, IconTile, PageHeader, Section, Stat, StatRow } from '../components/ui'
import { MEAL_VISUALS } from '../components/visuals'
import { tokens } from '../theme'

function isoDay(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const today = () => isoDay(new Date())

type TrendItem = {
  meal_date: string; meal_type: string
  energy_kcal: number | null; protein_g: number | null
  target_energy_kcal: number | null; target_protein_g: number | null
}
type DayPoint = { date: string; label: string; meals: number; energy: number | null; target: number | null; unknownEnergy: boolean }

/** 逐日加總；沒有紀錄的日子留空，讓時間軸連續。 */
function byDay(items: TrendItem[], since: string, days: number): DayPoint[] {
  const start = new Date(`${since}T00:00:00`)
  const points = new Map<string, DayPoint>()
  for (let i = 0; i < days; i++) {
    const d = new Date(start)
    d.setDate(start.getDate() + i)
    const date = isoDay(d)
    points.set(date, { date, label: `${d.getMonth() + 1}/${d.getDate()}`, meals: 0, energy: null, target: null, unknownEnergy: false })
  }
  const add = (a: number | null, v: number | null) => (v === null ? a : (a ?? 0) + v)
  for (const it of items) {
    const p = points.get(it.meal_date)
    if (!p) continue
    p.meals += 1
    p.energy = add(p.energy, it.energy_kcal)
    p.target = add(p.target, it.target_energy_kcal)
    p.unknownEnergy ||= it.energy_kcal === null
  }
  return [...points.values()]
}

const axis = { fontSize: 11, fill: tokens.inkMuted }

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: DayPoint }[] }) {
  const p = payload?.[0]?.payload
  if (!active || !p || p.meals === 0) return null
  return (
    <Box sx={{ bgcolor: tokens.paper, border: `1px solid ${tokens.line}`, px: 1.5, py: 1, borderRadius: 1,
      boxShadow: '0 6px 18px rgba(63,58,52,0.08)' }}>
      <Typography variant="caption" component="p" sx={{ color: tokens.inkMuted }}>{p.date}・{p.meals} 餐</Typography>
      <Typography variant="body2">熱量 {p.energy === null ? '未知' : `${Math.round(p.energy)} kcal`}</Typography>
      {p.target !== null && <Typography variant="caption" component="p">目標 {Math.round(p.target)} kcal</Typography>}
      <Typography variant="caption" component="p" sx={{ color: tokens.indigo }}>點一下查看當天紀錄</Typography>
    </Box>
  )
}

function TrendChart({ data, selected, onPick }: { data: DayPoint[]; selected: string; onPick: (date: string) => void }) {
  const barColor = tokens.indigo
  return (
    <Box sx={{ height: 220, mx: -1 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
          onClick={(e) => { const d = e?.activeLabel !== undefined ? data.find((p) => p.label === e.activeLabel) : undefined; if (d?.meals) onPick(d.date) }}>
          <CartesianGrid vertical={false} stroke={tokens.line} />
          <XAxis dataKey="label" tick={axis} tickLine={false} axisLine={{ stroke: tokens.line }} interval="preserveStartEnd" minTickGap={16} />
          <YAxis tick={axis} tickLine={false} axisLine={false} width={44}
            domain={[0, (max: number) => Math.ceil((max * 1.15) / 10) * 10]} allowDecimals={false} />
          <Tooltip cursor={{ fill: 'rgba(63,58,52,0.04)' }} content={<ChartTooltip />} />
          <Bar dataKey="energy" name="熱量" maxBarSize={22} radius={[2, 2, 0, 0]} fill={barColor} fillOpacity={0.8}
            style={{ cursor: 'pointer' }} isAnimationActive={false}
            shape={(props: { x?: number; y?: number; width?: number; height?: number; payload?: DayPoint }) => (
              <rect x={props.x} y={props.y} width={props.width} height={Math.max(0, props.height ?? 0)} rx={2}
                fill={barColor} fillOpacity={props.payload?.date === selected ? 1 : 0.55} />
            )} />
          {/* 目標畫成每天一條短刻度，不連線：沒紀錄的日子沒有目標可比 */}
          <Line dataKey="target" name="目標" stroke="none" isAnimationActive={false} activeDot={false}
            dot={(props: { cx?: number; cy?: number; index?: number; value?: number | null }) => (
              props.value === null || props.value === undefined || props.cy === undefined || props.cx === undefined
                ? <g key={props.index} />
                : <line key={props.index} x1={props.cx - 9} x2={props.cx + 9} y1={props.cy} y2={props.cy} stroke={tokens.brick} strokeWidth={2} />
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
          <Box sx={dashed ? { width: 14, borderTop: `2px solid ${color}` } : { width: 10, height: 10, bgcolor: color, opacity: 0.8, borderRadius: '2px' }} />
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
    // 截止日用本機今天：預先安排的未來餐食不列入統計
    queryFn: async () => (await api.get<{ since: string; until: string; items: TrendItem[] }>('/meal-plans/trend',
      { params: { days, until: today() } })).data,
  })
  const data = useMemo(() => (trend.data ? byDay(trend.data.items, trend.data.since, days) : []), [trend.data, days])
  const items = trend.data?.items ?? []
  const withEnergy = items.filter((i) => i.energy_kcal !== null && i.target_energy_kcal)
  const reach = withEnergy.length
    ? withEnergy.reduce((s, i) => s + (i.energy_kcal as number) / (i.target_energy_kcal as number), 0) / withEnergy.length
    : null
  const protein = items.filter((i) => i.protein_g !== null && i.target_protein_g)
  const proteinReach = protein.length
    ? protein.reduce((s, i) => s + (i.protein_g as number) / (i.target_protein_g as number), 0) / protein.length
    : null
  const tone = (key: 'energy_kcal' | 'protein_g', r: number | null) =>
    (r === null ? 'default' as const : withinTarget(key, r) ? 'good' as const : 'warn' as const)

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
              tone={tone('energy_kcal', reach)} note={reach === null ? '沒有可比較的紀錄' : withinTarget('energy_kcal', reach) ? '平均未超過目標 10%' : '平均超過目標 10%'} />
            <Stat label="蛋白質達成" value={proteinReach === null ? '—' : Math.round(proteinReach * 100)}
              unit={proteinReach === null ? undefined : '%'} tone={tone('protein_g', proteinReach)}
              note={proteinReach === null ? undefined : withinTarget('protein_g', proteinReach) ? '平均達目標 90% 以上' : '平均不足目標 90%'} />
            <Stat label="成本" value="—" note="價格整合後提供" />
          </StatRow>
          {items.length === 0 ? (
            <Typography variant="body2" color="text.secondary">近 {days} 天沒有已採用的餐食，採用菜單後這裡會出現趨勢。</Typography>
          ) : (
            <Box>
              <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 2, flexWrap: 'wrap', mb: 1 }}>
                <Typography variant="body2" sx={{ fontWeight: 500, flexGrow: 1 }}>每日熱量（kcal）</Typography>
                <Legend items={[['實際', tokens.indigo, false], ['目標', tokens.brick, true]]} />
              </Box>
              <TrendChart data={data} selected={selected} onPick={onPick} />
            </Box>
          )}
        </Stack>
      )}
    </Section>
  )
}

/** 單筆已採用餐食：顯示的都是採用當下保存的快照，來源之後更新不會改變。 */
function PlanCard({ plan }: { plan: MealPlanDetail }) {
  if (plan.result_version != null) {
    return <Section title={`${MEAL_LABELS[plan.meal_type]}・${plan.dishes.map((d) => d.dish_name).join('、')}`}>
      <Button component={Link} to={`/generation-history?date=${plan.meal_date}`}>
        查看完整餐食、營養與採購明細
      </Button>
    </Section>
  }
  const s = plan.snapshot
  const energy = toNumber(s.nutrition?.totals?.energy_kcal)
  const protein = toNumber(s.nutrition?.totals?.protein_g)
  const selected = s.selected ?? []
  const dishName = new Map(selected.map((r) => [r.recipe_id, r.recipe_name]))
  const names = new Map((s.people ?? []).map((p) => [p.member_id, p.name ?? '成員']))
  return (
    <Section title={
      <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 1.5 }}>
        {MEAL_VISUALS[plan.meal_type] && <IconTile icon={MEAL_VISUALS[plan.meal_type].icon} tone={MEAL_VISUALS[plan.meal_type].tone} size={34} />}
        {MEAL_LABELS[plan.meal_type]}
      </Box>
    } aside={`採用於 ${new Date(plan.adopted_at).toLocaleString()}`}>
      <Stack spacing={2.5}>
        <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}><Chip size="small" label="資料庫菜單" /></Box>
        <StatRow>
          <Stat label="整餐熱量" value={energy === null ? '未知' : Math.round(energy)} unit={energy === null ? undefined : 'kcal'}
            note={s.targets?.energy_kcal ? `目標 ${Math.round(toNumber(s.targets.energy_kcal) ?? 0)} kcal` : undefined} />
          <Stat label="整餐蛋白質" value={protein === null ? '未知' : protein.toFixed(1)} unit={protein === null ? undefined : 'g'}
            note={s.targets?.protein_g ? `目標 ${Math.round(toNumber(s.targets.protein_g) ?? 0)} g` : undefined} />
          <Stat label="菜數" value={plan.dishes.length} unit="道" note={s.staple_choice ? `主食：${s.staple_choice}` : undefined} />
          <Stat label="用餐份數" value={s.people?.length ?? '—'} unit={s.people ? '人' : undefined} />
        </StatRow>
        {selected.length > 0 && (
          <DishList selected={selected} dishes={s.dishes} staple={s.staple ?? null} stapleName={s.staple_choice ?? null} />
        )}
        {(s.people?.length ?? 0) > 0 && (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 500, mb: 1.5 }}>每人營養（這一餐／每日建議）</Typography>
            <PersonNutrition people={s.people ?? []} meta={s.daily_reference_meta} names={names}
              dishName={dishName} staple={s.staple_choice ?? null} />
          </Box>
        )}
      </Stack>
    </Section>
  )
}

/** 已採用紀錄：上方趨勢圖、下方依日期回看當日各餐別的完整快照。只有按「採用」的餐食會出現在這裡。 */
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
    queryFn: async () => (await api.get<{ meal_plans: MealPlanDetail[] }>('/meal-plans', { params: { date } })).data.meal_plans,
  })

  return (
    <Stack spacing={4}>
      <PageHeader eyebrow="History" title="已採用紀錄"
        description="只列出按「採用」保存的餐食，不代表實際已食用；僅查看的菜單不會出現。" />
      {dates.data && dates.data.length === 0 ? (
        <EmptyState icon={<HistoryEduOutlined />} title="還沒有採用過菜單"
          description="在規劃餐食頁的確認步驟按「採用這份菜單」後，這裡會記錄每一餐，並畫出熱量的趨勢。" />
      ) : (
        <>
          <Trends selected={date} onPick={pick} />
          <Stack spacing={2}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
              <Typography variant="h6" component="h2" sx={{ flexGrow: 1 }}>{date} 的餐食</Typography>
              <TextField type="date" size="small" label="日期" value={date} sx={{ width: 180 }}
                onChange={(e) => e.target.value && pick(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
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
