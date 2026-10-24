import AddOutlined from '@mui/icons-material/AddOutlined'
import ChevronRightOutlined from '@mui/icons-material/ChevronRightOutlined'
import RestaurantMenuOutlined from '@mui/icons-material/RestaurantMenuOutlined'
import {
  Alert, Box, Button, Card, CardActionArea, CardContent, Chip, CircularProgress, Stack, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { api, errorMessage } from '../api/client'
import { MEAL_LABELS, type MealRequest, type MealRequestSummary } from '../api/types'
import { EmptyState, PageHeader } from '../components/ui'
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
      <PageHeader eyebrow="Plan" title="規劃餐食"
        description="每份問卷規劃一餐：選日期與用餐成員、填預算和家中食材，系統會算出全家剛好的份量。"
        action={list.data && list.data.length > 0 ? newButton : undefined} />
      {create.isError && <Alert severity="error">{errorMessage(create.error)}</Alert>}
      {list.isPending && <CircularProgress />}
      {list.isError && <Alert severity="error">{errorMessage(list.error)}</Alert>}
      {list.data?.length === 0 && (
        <EmptyState icon={<RestaurantMenuOutlined />} title="規劃你家的第一餐"
          description="建立一份問卷，告訴我們誰要吃、預算多少、家裡有什麼，就能得到一份營養剛好的菜單。"
          action={newButton} />
      )}
      <Stack spacing={1.5}>
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
