import {
  Alert, Box, Button, Card, CardActionArea, CardContent, Chip, CircularProgress, Stack, Typography,
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { api, errorMessage } from '../api/client'
import { MEAL_LABELS, type MealRequest, type MealRequestSummary } from '../api/types'

const STATUS_LABELS: Record<string, string> = { draft: '草稿' }

export default function MealRequestsPage() {
  const navigate = useNavigate()
  const list = useQuery({
    queryKey: ['meal-requests'],
    queryFn: async () => (await api.get<{ meal_requests: MealRequestSummary[] }>('/meal-requests')).data.meal_requests,
  })
  const create = useMutation({
    mutationFn: async () => (await api.post<MealRequest>('/meal-requests', {})).data,
    onSuccess: (row) => navigate(`/plan/${row.id}`),
  })

  return (
    <Stack spacing={3}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <Typography variant="h5" component="h1" sx={{ flexGrow: 1 }}>規劃餐食</Typography>
        <Button variant="contained" onClick={() => create.mutate()} disabled={create.isPending}>新增問卷</Button>
      </Box>
      {create.isError && <Alert severity="error">{errorMessage(create.error)}</Alert>}
      {list.isPending && <CircularProgress />}
      {list.isError && <Alert severity="error">{errorMessage(list.error)}</Alert>}
      {list.data?.length === 0 && <Typography color="text.secondary">還沒有問卷。按「新增問卷」開始規劃一餐。</Typography>}
      <Stack spacing={1}>
        {list.data?.map((r) => (
          <Card key={r.id} variant="outlined">
            <CardActionArea onClick={() => navigate(`/plan/${r.id}`)}>
              <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                <Typography sx={{ flexGrow: 1 }}>
                  {r.meal_date ?? '未選日期'}・{r.meal_type ? MEAL_LABELS[r.meal_type] : '未選餐別'}
                </Typography>
                <Chip size="small" label={STATUS_LABELS[r.status] ?? r.status} />
                <Typography variant="body2" color="text.secondary">版本 {r.version}</Typography>
              </CardContent>
            </CardActionArea>
          </Card>
        ))}
      </Stack>
    </Stack>
  )
}
