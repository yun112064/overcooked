import AddOutlined from '@mui/icons-material/AddOutlined'
import ChevronRightOutlined from '@mui/icons-material/ChevronRightOutlined'
import RestaurantMenuOutlined from '@mui/icons-material/RestaurantMenuOutlined'
import {
  Alert, Box, Button, Card, CardActionArea, CardContent, Chip, CircularProgress, Stack, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api, errorMessage } from '../api/client'
import { MEAL_LABELS, type MealRequest, type MealRequestSummary, type Me, type Member } from '../api/types'
import { AvatarStack, EmptyState, IconTile, PageHeader } from '../components/ui'
import { MEAL_VISUALS } from '../components/visuals'
import { tokens } from '../theme'

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿', queued: '排隊中', running: '生成中', awaiting_decision: '待決定', result_ready: '已有結果',
  failed: '失敗', confirmed: '已採用',
}
const STATUS_COLORS: Record<string, 'default' | 'primary' | 'secondary' | 'warning' | 'error' | 'success'> = {
  queued: 'primary', running: 'primary', awaiting_decision: 'warning', result_ready: 'primary',
  failed: 'error', confirmed: 'success',
}
const WEEKDAYS = '日一二三四五六'

/** 2026-10-24 → { md: '10/24', weekday: '週六' } */
function dateParts(iso: string | null) {
  if (!iso) return null
  const [y, m, d] = iso.split('-').map(Number)
  return { md: `${m}/${d}`, weekday: `週${WEEKDAYS[new Date(y, m - 1, d).getDay()]}` }
}

// 使用者本機時區的今天（YYYY-MM-DD）；toISOString 是 UTC，台灣早上 8 點前會變成昨天
function localToday() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

type TrendItem = { energy_kcal: string | null; target_energy_kcal: string | null; cost_total: string | null }

/** 依本機時間的問候語 */
function greeting() {
  const h = new Date().getHours()
  return h < 11 ? '早安' : h < 17 ? '午安' : '晚安'
}

/** 頁首歡迎區：問候、今天日期、家庭成員，以及近 7 天的已採用摘要（每頁唯一的主要視覺）。 */
function PlanHero({ action }: { action?: ReactNode }) {
  const me = useQuery({ queryKey: ['me'], queryFn: async () => (await api.get<Me>('/auth/me')).data })
  const members = useQuery({
    queryKey: ['members'],
    queryFn: async () => (await api.get<{ members: Member[] }>('/members')).data.members,
  })
  const week = useQuery({
    queryKey: ['meal-plan-trend', 7, localToday()],
    queryFn: async () => (await api.get<{ items: TrendItem[] }>('/meal-plans/trend',
      { params: { days: 7, until: localToday() } })).data.items,
  })
  const now = new Date()
  const items = week.data ?? []
  const withTarget = items.filter((i) => i.energy_kcal !== null && i.target_energy_kcal)
  const reach = withTarget.length
    ? withTarget.reduce((s, i) => s + Number(i.energy_kcal) / Number(i.target_energy_kcal), 0) / withTarget.length : null
  const costs = items.filter((i) => i.cost_total !== null).map((i) => Number(i.cost_total))
  const stats: [string, string, string?][] = [
    ['近 7 天採用', `${items.length}`, '餐'],
    ['熱量達成', reach === null ? '—' : `${Math.round(reach * 100)}`, reach === null ? undefined : '%'],
    ['平均每餐成本', costs.length ? `${Math.round(costs.reduce((a, b) => a + b, 0) / costs.length)}` : '—', costs.length ? '元' : undefined],
  ]
  return (
    <Box sx={{
      position: 'relative', overflow: 'hidden', borderRadius: 2, bgcolor: tokens.sand,
      border: `1px solid ${tokens.line}`, px: { xs: 2.5, sm: 4 }, py: { xs: 3, sm: 4 },
    }}>
      {/* 裝飾用的大字「食」浮水印；不影響閱讀 */}
      <Typography aria-hidden sx={{
        position: 'absolute', right: { xs: -12, sm: 24 }, top: { xs: -24, sm: -36 }, fontFamily: tokens.serif,
        fontSize: { xs: 150, sm: 220 }, lineHeight: 1, color: tokens.brick, opacity: 0.07, userSelect: 'none', pointerEvents: 'none',
      }}>食</Typography>
      <Box sx={{ position: 'relative' }}>
        <Typography variant="overline" component="p">
          {now.getMonth() + 1} 月 {now.getDate()} 日・星期{WEEKDAYS[now.getDay()]}
        </Typography>
        <Typography variant="h4" component="h1" sx={{ mt: 0.5 }}>
          {me.data?.current_household?.name ?? ''}，{greeting()}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1, maxWidth: 520 }}>
          填一份問卷，告訴我們誰要吃、預算多少、家裡有什麼，就能得到全家營養剛好的一餐。
        </Typography>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 2.5, flexWrap: 'wrap' }}>
          {members.data && members.data.length > 0 && <AvatarStack names={members.data.map((m) => m.display_name)} ring={tokens.sand} />}
          {action}
        </Box>
        <Box sx={{
          mt: 3, pt: 2.5, borderTop: `1px solid ${tokens.lineStrong}`,
          display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 2, maxWidth: 520,
        }}>
          {stats.map(([label, value, unit]) => (
            <Box key={label}>
              <Typography variant="caption" component="p" sx={{ letterSpacing: '0.12em' }}>{label}</Typography>
              <Typography component="p" sx={{ fontFamily: tokens.serif, fontSize: '1.5rem', lineHeight: 1.3 }}>
                {value}{unit && <Box component="span" sx={{ fontSize: '0.8rem', ml: 0.5, color: tokens.inkSoft }}>{unit}</Box>}
              </Typography>
            </Box>
          ))}
        </Box>
      </Box>
    </Box>
  )
}

export default function MealRequestsPage() {
  const navigate = useNavigate()
  const list = useQuery({
    queryKey: ['meal-requests'],
    queryFn: async () => (await api.get<{ meal_requests: MealRequestSummary[] }>('/meal-requests')).data.meal_requests,
  })
  const create = useMutation({
    mutationFn: async () => (await api.post<MealRequest>('/meal-requests', { meal_date: localToday() })).data,
    onSuccess: (row) => navigate(`/plan/${row.id}`),
  })

  const newButton = (
    <Button variant="contained" startIcon={<AddOutlined />} onClick={() => create.mutate()} disabled={create.isPending}>
      新增問卷
    </Button>
  )

  return (
    <Stack spacing={4}>
      <PlanHero action={newButton} />
      {create.isError && <Alert severity="error">{errorMessage(create.error)}</Alert>}
      {list.isPending && <CircularProgress />}
      {list.isError && <Alert severity="error">{errorMessage(list.error)}</Alert>}
      {list.data && list.data.length > 0 && (
        <PageHeader title="我的問卷" description="點進問卷可以修改、生成菜單，或查看已採用的結果。" />
      )}
      {list.data?.length === 0 && (
        <EmptyState icon={<RestaurantMenuOutlined />} title="規劃你家的第一餐"
          description="建立一份問卷，告訴我們誰要吃、預算多少、家裡有什麼，就能得到一份營養剛好的菜單。"
          action={newButton} />
      )}
      <Stack spacing={1.5} className="rise-in">
        {list.data?.map((r) => {
          const date = dateParts(r.meal_date)
          return (
            <Card key={r.id}>
              <CardActionArea onClick={() => navigate(`/plan/${r.id}`)}>
                <CardContent sx={{ display: 'flex', alignItems: 'center', gap: { xs: 2, sm: 3 } }}>
                  <Box sx={{ width: 64, flexShrink: 0, textAlign: 'center', borderRight: `1px solid ${tokens.line}`, pr: { xs: 2, sm: 3 } }}>
                    <Typography sx={{ fontFamily: tokens.serif, fontSize: '1.35rem', lineHeight: 1.2 }}>{date?.md ?? '—'}</Typography>
                    <Typography variant="caption">{date?.weekday ?? '未選日期'}</Typography>
                  </Box>
                  {r.meal_type && <IconTile icon={MEAL_VISUALS[r.meal_type].icon} tone={MEAL_VISUALS[r.meal_type].tone} />}
                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Typography sx={{ fontWeight: 500, letterSpacing: '0.08em' }}>
                      {r.meal_type ? MEAL_LABELS[r.meal_type] : '未選餐別'}
                    </Typography>
                    <Typography variant="caption">草稿第 {r.version} 版</Typography>
                  </Box>
                  <Chip size="small" variant={r.status === 'draft' ? 'filled' : 'outlined'}
                    color={STATUS_COLORS[r.status] ?? 'default'} label={STATUS_LABELS[r.status] ?? r.status} />
                  <ChevronRightOutlined sx={{ color: tokens.inkMuted, display: { xs: 'none', sm: 'block' } }} />
                </CardContent>
              </CardActionArea>
            </Card>
          )
        })}
      </Stack>
    </Stack>
  )
}
