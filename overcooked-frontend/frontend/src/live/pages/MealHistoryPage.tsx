import { Alert, Box, Button, Chip, CircularProgress, IconButton, Stack, Typography } from '@mui/material'
import { ArrowLeft, CalendarDays, ChevronLeft, ChevronRight, Utensils } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { api, errorMessage } from '../api/client'
import { MEAL_LABELS } from '../api/types'
import HistoryPlanDetails from '../components/HistoryPlanDetails'
import { PageHeader, Section } from '../components/ui'
import { historyDate, isoDay, shiftMonth } from '../history/calendar'
import type { SavedPlan } from '../history/snapshots'
import { GenerationHistoryTrends } from './GenerationHistoryPage'

const mealOrder = { breakfast: 0, lunch: 1, dinner: 2 }
type RecordDate = { meal_date: string; count: number }

function HistoryDay({ date, onPick, dates }: { date: string; onPick: (date: string) => void; dates: RecordDate[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const detailId = useId()
  const details = useRef<HTMLElement>(null)
  const plans = useQuery({
    queryKey: ['meal-plans', date],
    queryFn: async () => (await api.get<{ meal_plans: SavedPlan[] }>('/meal-plans', { params: { date } })).data.meal_plans,
  })
  const records = plans.isError ? [] : [...(plans.data ?? [])].sort((a, b) => mealOrder[a.meal_type] - mealOrder[b.meal_type])
  const selected = records.find(plan => plan.id === selectedId)
  useEffect(() => {
    if (selectedId) details.current?.scrollIntoView({ behavior: 'auto', block: 'start' })
  }, [selectedId])
  const [year, month, day] = date.split('-').map(Number)
  const days = new Date(year, month, 0).getDate()
  const start = new Date(`${date.slice(0, 7)}-01T12:00:00`).getDay()
  const marked = new Map(dates.map(item => [item.meal_date, item.count]))
  if (plans.data && !plans.isError) marked.set(date, records.length)
  return <>
    <Box className="live-history-top">
      <Section title={<Box component="span" className="live-calendar-controls">
        <IconButton aria-label="前一月" onClick={() => onPick(shiftMonth(date, -1))}><ChevronLeft size={18} /></IconButton>
        <span>{year} 年 {month} 月</span>
        <IconButton aria-label="下一月" onClick={() => onPick(shiftMonth(date, 1))}><ChevronRight size={18} /></IconButton>
      </Box>}>
        <Box className="live-calendar-weekdays" aria-hidden="true">{['日', '一', '二', '三', '四', '五', '六'].map(label => <span key={label}>{label}</span>)}</Box>
        <Box className="live-calendar-days" role="group" aria-label="月曆日期">
          {Array.from({ length: start }, (_, index) => <span key={`blank-${index}`} aria-hidden="true" />)}
          {Array.from({ length: days }, (_, index) => {
            const value = `${date.slice(0, 7)}-${String(index + 1).padStart(2, '0')}`
            const count = marked.get(value) ?? 0
            return <button type="button" className="live-calendar-day" key={value} aria-pressed={date === value}
              aria-current={value === isoDay(new Date()) ? 'date' : undefined}
              aria-label={`${year} 年 ${month} 月 ${index + 1} 日${count ? `，${count} 筆已採用紀錄` : ''}`} onClick={() => onPick(value)}>
              {index + 1}{count > 0 && <span className="live-calendar-dot" aria-hidden="true" />}
            </button>
          })}
        </Box>
        <Typography component="p" variant="caption" className="live-calendar-key"><span />近期已採用紀錄</Typography>
        <Typography component="p" variant="caption">其他日期仍可點選查詢。</Typography>
      </Section>
      <Section title={`${year} 年 ${month} 月 ${day} 日`}>
        <Stack spacing={2}>
          {plans.isPending && <CircularProgress size={24} aria-label="載入當日紀錄" />}
          {plans.isError && <Alert severity="error" action={<Button color="inherit" size="small" onClick={() => plans.refetch()}>重試</Button>}>{errorMessage(plans.error)}</Alert>}
          {plans.data && !plans.isError && records.length === 0 && <Typography variant="body2" color="text.secondary">這天尚無已採用的餐食。</Typography>}
          {records.map(plan => <Box component="article" className="live-history-record" key={plan.id} data-selected={selectedId === plan.id}>
            <Box className="live-history-record-heading"><Typography component="h3" variant="subtitle1"><Utensils size={17} />{MEAL_LABELS[plan.meal_type]}</Typography>
              <Chip size="small" label={'mode_label' in plan ? plan.mode_label : '資料庫菜單'} /></Box>
            <Typography variant="body2">{plan.dishes.map(dish => dish.dish_name).join('、') || '此紀錄未保存菜名'}</Typography>
            {'price_exception_accepted' in plan && plan.price_exception_accepted && <Typography variant="caption" color="warning.main">已接受缺價例外</Typography>}
            <Button variant="outlined" aria-controls={detailId} aria-expanded={selectedId === plan.id} onClick={() => setSelectedId(plan.id)}>查看內容</Button>
          </Box>)}
        </Stack>
      </Section>
    </Box>
    {selected && <Box component="section" className="live-history-detail" ref={details} id={detailId} aria-label="餐食內容">
      <Stack direction="row" spacing={1} sx={{ alignItems: 'baseline', flexWrap: 'wrap', mb: 2 }}>
        <Typography component="h2" variant="h6">{date.replaceAll('-', ' / ')} · {MEAL_LABELS[selected.meal_type]}</Typography>
        <Typography variant="caption">採用於 {new Date(selected.adopted_at).toLocaleString()}</Typography>
      </Stack>
      <HistoryPlanDetails key={selected.id} plan={selected} />
    </Box>}
  </>
}

export default function MealHistoryPage() {
  const [params, setParams] = useSearchParams()
  const date = historyDate(params.get('date'))
  const pick = (value: string) => setParams({ date: value })
  const dates = useQuery({
    queryKey: ['meal-plan-dates'],
    queryFn: async () => (await api.get<{ dates: RecordDate[] }>('/meal-plans')).data.dates,
  })
  return <Stack spacing={3}>
    <PageHeader eyebrow="History" title={<Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 1.5 }}><CalendarDays size={28} />已採用餐食紀錄</Box>}
      description="只列出按「採用」保存的餐食，不代表實際已食用；僅查看的菜單不會出現。" />
    {dates.isError && <Alert severity="warning" action={<Button size="small" color="inherit" onClick={() => dates.refetch()}>重試</Button>}>近期紀錄日期載入失敗，仍可點選日期查詢。{errorMessage(dates.error)}</Alert>}
    {dates.isPending && <Typography variant="caption" role="status">正在載入近期紀錄日期…</Typography>}
    <HistoryDay key={date} date={date} dates={dates.data ?? []} onPick={pick} />
    <GenerationHistoryTrends selected={date} onPick={pick} />
    <Box><Button component={Link} to="/home" variant="outlined" startIcon={<ArrowLeft size={18} />}>返回首頁</Button></Box>
  </Stack>
}
