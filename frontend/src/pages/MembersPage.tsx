import {
  Alert, Box, Button, Card, CardActions, CardContent, Chip, CircularProgress, Dialog, DialogActions,
  DialogContent, DialogTitle, Stack, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorMessage } from '../api/client'
import { ACTIVITY_LABELS, GOAL_LABELS, type Member, type MemberOptions } from '../api/types'
import MemberFormDialog from '../components/MemberFormDialog'

export default function MembersPage() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<Member | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [deleting, setDeleting] = useState<Member | null>(null)

  const members = useQuery({
    queryKey: ['members'],
    queryFn: async () => (await api.get<{ members: Member[] }>('/members')).data.members,
  })
  const options = useQuery({
    queryKey: ['member-options'],
    queryFn: async () => (await api.get<MemberOptions>('/members/options')).data,
    staleTime: Infinity,
  })
  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/members/${id}`),
    onSuccess: async () => {
      setDeleting(null)
      await queryClient.invalidateQueries({ queryKey: ['members'] })
    },
  })

  const openForm = (member: Member | null) => { setEditing(member); setFormOpen(true) }

  return (
    <Stack spacing={3}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <Typography variant="h5" component="h1" sx={{ flexGrow: 1 }}>家庭成員</Typography>
        <Button variant="contained" onClick={() => openForm(null)} disabled={!options.data}>新增成員</Button>
      </Box>

      {(members.isPending || options.isPending) && <CircularProgress />}
      {members.isError && <Alert severity="error">{errorMessage(members.error)}</Alert>}
      {options.isError && <Alert severity="error">{errorMessage(options.error)}</Alert>}
      {members.data?.length === 0 && (
        <Typography color="text.secondary">還沒有成員。新增成員後，規劃餐食時就能選擇誰要用餐。</Typography>
      )}

      <Stack spacing={2}>
        {members.data?.map((m) => (
          <Card key={m.id} variant="outlined">
            <CardContent>
              <Typography variant="h6" component="h2">{m.display_name}</Typography>
              <Typography color="text.secondary" variant="body2">
                {m.age_years} 歲・{m.equation_sex === 'F' ? '女' : '男'}・{m.weight_kg} 公斤・
                {ACTIVITY_LABELS[m.activity_category]}
              </Typography>
              <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: 'wrap', rowGap: 1 }}>
                <Chip size="small" label={GOAL_LABELS[m.goal]} />
                <Chip size="small" label={`食量 ${m.portion}n`} />
                {m.allergens.map((a) => (
                  <Chip key={a.ingredient_id} size="small" color="error" variant="outlined" label={`過敏：${a.name}`} />
                ))}
                {m.avoided.map((a) => (
                  <Chip key={a.ingredient_id} size="small" color="warning" variant="outlined" label={`禁吃：${a.name}`} />
                ))}
              </Stack>
            </CardContent>
            <CardActions>
              <Button onClick={() => openForm(m)} disabled={!options.data}>編輯</Button>
              <Button color="error" onClick={() => setDeleting(m)}>刪除</Button>
            </CardActions>
          </Card>
        ))}
      </Stack>

      {options.data && (
        <MemberFormDialog open={formOpen} member={editing} options={options.data} onClose={() => setFormOpen(false)} />
      )}

      <Dialog open={!!deleting} onClose={() => setDeleting(null)}>
        <DialogTitle>刪除成員</DialogTitle>
        <DialogContent>
          <Typography>確定要刪除「{deleting?.display_name}」嗎？刪除後無法復原。</Typography>
          {remove.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorMessage(remove.error)}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleting(null)}>取消</Button>
          <Button color="error" variant="contained" disabled={remove.isPending}
            onClick={() => deleting && remove.mutate(deleting.id)}>刪除</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
