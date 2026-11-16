import { zodResolver } from '@hookform/resolvers/zod'
import { Plus, Users } from 'lucide-react'
import {
  Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { api, errorMessage } from '../api/client'
import type { Household } from '../api/types'
import MembersPage from './MembersPage'

const schema = z.object({ name: z.string().trim().min(1, '請輸入家庭名稱').max(100, '最多 100 個字') })
type FormValues = z.infer<typeof schema>

type HouseholdList = { households: Household[]; current_household_id: string | null }

// 與目前家庭無關的快取（帳號、家庭清單、主食選項、團隊服務狀態、食材目錄）；其餘都依家庭區分
const GLOBAL_QUERY_KEYS = new Set(['me', 'households', 'staple-options', 'team-status', 'ingredients'])

export default function HouseholdPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [createOpen, setCreateOpen] = useState(false)
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
    // 成功與失敗都重新確認伺服器選定的家庭；網路中斷不代表切換沒有生效。
    await list.refetch()
    navigate('/members')
  }

  const create = useMutation({
    mutationFn: async (values: FormValues) => (await api.post<Household>('/household', values)).data,
    onSuccess: () => { reset(); select.reset(); setCreateOpen(false) },
    onSettled: afterChange,
  })
  const select = useMutation({
    mutationFn: async (id: string) => (await api.post<Household>('/household/select', { household_id: id })).data,
    onSettled: afterChange,
  })

  const changing = create.isPending || select.isPending
  const activeId = list.data?.current_household_id
  const active = list.data?.households.find(h => h.id === activeId)
  const closeCreate = () => { if (!create.isPending) setCreateOpen(false) }

  return <Stack spacing={3}>
    <Box className="live-family-title">
      <Box className="live-family-title-icon"><Users size={23} aria-hidden="true" /></Box>
      <Typography component="h1" variant="h4">家庭與成員</Typography>
    </Box>
    <Box className="live-family-toolbar">
      <TextField select label="目前家庭" size="small" value={active?.id ?? ''}
        disabled={changing || list.isPending || list.isError || !list.data?.households.length}
        onChange={event => { if (event.target.value !== activeId) select.mutate(event.target.value) }}>
        <MenuItem value="" disabled>請選擇家庭</MenuItem>
        {list.data?.households.map(h => <MenuItem key={h.id} value={h.id}>{h.name}</MenuItem>)}
      </TextField>
      <Button variant="outlined" startIcon={<Plus size={18} />} disabled={changing}
        onClick={() => { reset(); create.reset(); setCreateOpen(true) }}>建立家庭</Button>
    </Box>
    {select.isError && <Alert severity="error">{errorMessage(select.error)}</Alert>}
    {list.isError && <Alert severity="error" action={<Button color="inherit" onClick={() => { void list.refetch() }}>重試</Button>}>
      {errorMessage(list.error)}
    </Alert>}
    {(changing || list.isPending || list.isFetching) && <Box className="live-family-loading" role="status">
      <CircularProgress size={22} /><Typography>{changing ? '正在更新家庭，請稍候…' : '正在讀取家庭…'}</Typography>
    </Box>}
    {!changing && !list.isFetching && !list.isError && list.data && (
      active ? <MembersPage key={active.id} householdId={active.id} /> : <Box className="live-family-empty">
        <Users size={34} aria-hidden="true" />
        <Typography>{list.data.households.length ? '請先選擇家庭，再管理成員。' : '尚未建立家庭，先建立你的第一個家庭。'}</Typography>
        <Button variant="outlined" onClick={() => { reset(); create.reset(); setCreateOpen(true) }}>建立家庭</Button>
      </Box>
    )}
    <Dialog open={createOpen} onClose={closeCreate} fullWidth maxWidth="xs">
      <DialogTitle>建立家庭</DialogTitle>
      <Box component="form" noValidate onSubmit={handleSubmit(values => create.mutate(values))}>
        <DialogContent>
          <TextField label="家庭名稱" placeholder="王家" fullWidth autoFocus {...register('name')}
            disabled={create.isPending} error={!!errors.name} helperText={errors.name?.message}
            slotProps={{ htmlInput: { maxLength: 100 } }} />
          {create.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorMessage(create.error)}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={closeCreate} disabled={create.isPending}>取消</Button>
          <Button type="submit" variant="contained" disabled={create.isPending}>{create.isPending ? '建立中…' : '建立'}</Button>
        </DialogActions>
      </Box>
    </Dialog>
  </Stack>
}
