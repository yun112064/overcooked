import { zodResolver } from '@hookform/resolvers/zod'
import ChevronRightOutlined from '@mui/icons-material/ChevronRightOutlined'
import HomeOutlined from '@mui/icons-material/HomeOutlined'
import {
  Alert, Box, Button, Card, CardActionArea, CardContent, Chip, CircularProgress, Stack, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { api, errorMessage } from '../api/client'
import type { Household } from '../api/types'
import { PageHeader, Section } from '../components/ui'
import { tokens } from '../theme'

const schema = z.object({ name: z.string().trim().min(1, '請輸入家庭名稱').max(100, '最多 100 個字') })
type FormValues = z.infer<typeof schema>

type HouseholdList = { households: Household[]; current_household_id: string | null }

// 與目前家庭無關的快取（帳號、家庭清單、主食選項、團隊服務狀態、食材目錄）；其餘都依家庭區分
const GLOBAL_QUERY_KEYS = new Set(['me', 'households', 'staple-options', 'team-status', 'ingredients'])

export default function HouseholdPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const list = useQuery({
    queryKey: ['households'],
    queryFn: async () => (await api.get<HouseholdList>('/household')).data,
  })
  const { register, handleSubmit, reset, formState: { errors } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '' },
  })

  const afterChange = async () => {
    // 換家庭：成員、問卷、結果、採用紀錄、工作等都屬於上一個家庭，全部清掉，避免短暫顯示舊資料或點進 404；
    // 只保留與家庭無關的快取
    queryClient.removeQueries({ predicate: (q) => !GLOBAL_QUERY_KEYS.has(String(q.queryKey[0])) })
    await queryClient.invalidateQueries({ queryKey: ['me'] })
    await queryClient.invalidateQueries({ queryKey: ['households'] })
    navigate('/members')
  }

  const create = useMutation({
    mutationFn: async (values: FormValues) => (await api.post<Household>('/household', values)).data,
    onSuccess: async () => { reset(); await afterChange() },
  })
  const select = useMutation({
    mutationFn: async (id: string) => (await api.post<Household>('/household/select', { household_id: id })).data,
    onSuccess: afterChange,
  })

  return (
    <Stack spacing={4}>
      <PageHeader eyebrow="Household" title="選擇家庭"
        description="成員、問卷與採用紀錄都屬於某個家庭。切換家庭後，畫面只會顯示那個家庭的資料。" />
      {list.isPending && <CircularProgress />}
      {list.isError && <Alert severity="error">{errorMessage(list.error)}</Alert>}
      {select.isError && <Alert severity="error">{errorMessage(select.error)}</Alert>}
      {list.data && list.data.households.length === 0 && (
        <Typography color="text.secondary">尚未建立家庭，請先在下方建立。</Typography>
      )}
      <Stack spacing={1.5}>
        {list.data?.households.map((h) => {
          const active = h.id === list.data.current_household_id
          return (
            <Card key={h.id} sx={active ? { borderColor: tokens.indigo } : undefined}>
              <CardActionArea onClick={() => select.mutate(h.id)} disabled={select.isPending}>
                <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                  <Box sx={{ color: active ? tokens.indigo : tokens.inkMuted, display: 'flex' }}><HomeOutlined /></Box>
                  <Typography sx={{ flexGrow: 1, fontFamily: tokens.serif, fontSize: '1.1rem', letterSpacing: '0.08em' }}>{h.name}</Typography>
                  {active && <Chip size="small" color="primary" variant="outlined" label="目前家庭" />}
                  <ChevronRightOutlined sx={{ color: tokens.inkMuted }} />
                </CardContent>
              </CardActionArea>
            </Card>
          )
        })}
      </Stack>

      <Section title="建立新家庭">
        <Stack component="form" direction={{ xs: 'column', sm: 'row' }} spacing={2} noValidate
          onSubmit={handleSubmit((v) => create.mutate(v))}>
          <TextField label="家庭名稱" placeholder="王家" fullWidth {...register('name')}
            error={!!errors.name} helperText={errors.name?.message} />
          <Button type="submit" variant="contained" disabled={create.isPending} sx={{ flexShrink: 0, height: 56 }}>
            建立
          </Button>
        </Stack>
        {create.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorMessage(create.error)}</Alert>}
      </Section>
    </Stack>
  )
}
