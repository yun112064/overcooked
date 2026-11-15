import { ArrowRight, CircleHelp, Pencil, UserPlus, Users } from 'lucide-react'
import { Link } from 'react-router'
import DeleteOutlineOutlined from '@mui/icons-material/DeleteOutlineOutlined'
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog, DialogActions,
  DialogContent, DialogTitle, IconButton, Stack, Tooltip, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorMessage } from '../api/client'
import { GOAL_LABELS, type Member, type MemberOptions } from '../api/types'

export default function MembersPage({ householdId }: { householdId: string }) {
  const queryClient = useQueryClient()
  const [deleting, setDeleting] = useState<Member | null>(null)

  const members = useQuery({
    queryKey: ['members', householdId],
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

  const addButton = (
    <Button variant="outlined" startIcon={<UserPlus size={18} />} component={Link} to="/members/new" disabled={!options.data}>
      新增成員
    </Button>
  )

  return (
    <Stack spacing={4}>
      <Box className="live-family-members-heading">
        <Typography component="h2" variant="h6">家庭成員</Typography>
        {addButton}
      </Box>

      {(members.isPending || options.isPending) && <CircularProgress />}
      {members.isError && <Alert severity="error">{errorMessage(members.error)}</Alert>}
      {options.isError && <Alert severity="error">{errorMessage(options.error)}</Alert>}
      {members.data?.length === 0 && (
        <Box className="live-family-empty">
          <Users size={34} aria-hidden="true" />
          <Typography>尚未新增家庭成員</Typography>
          {addButton}
        </Box>
      )}

      <Box className="live-member-grid">
        {members.data?.map(m => <Card key={m.id} className="live-member-card">
          <CardContent>
            <Box className="live-member-heading">
              <Box className="live-member-avatar" aria-hidden="true">{m.display_name.slice(0, 1)}</Box>
              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography variant="caption">名稱</Typography>
                <Typography variant="h6" component="h3">{m.display_name}</Typography>
              </Box>
              <Tooltip title="刪除成員">
                <IconButton size="small" aria-label={`刪除 ${m.display_name}`} onClick={() => setDeleting(m)}>
                  <DeleteOutlineOutlined fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
            <Box component="dl" className="live-member-facts">
              <div><Typography component="dt" variant="caption">年齡</Typography><Typography component="dd">{m.age_years} 歲</Typography></div>
              <div><Typography component="dt" variant="caption">食量</Typography>
                <Typography component="dd" className="live-member-portion">{m.portion}n
                  <Tooltip title="1n 為標準食譜的一位成人餐份；食量會依選擇的倍數調整。">
                    <IconButton size="small" aria-label="食量說明"><CircleHelp size={15} /></IconButton>
                  </Tooltip>
                </Typography>
              </div>
              <div><Typography component="dt" variant="caption">飲食目標</Typography><Typography component="dd">{GOAL_LABELS[m.goal]}</Typography></div>
            </Box>
            <Box className="live-member-restrictions">
              <Typography variant="caption">過敏食材</Typography>
              <Box>{m.allergens.length ? m.allergens.map(a => <Chip key={a.ingredient_id} size="small" color="error" variant="outlined" label={a.name ?? a.ingredient_id} />)
                : <Typography variant="body2" color="text.secondary">無</Typography>}</Box>
            </Box>
            <Box className="live-member-restrictions">
              <Typography variant="caption">禁吃食材</Typography>
              <Box>{m.avoided.length ? m.avoided.map(a => <Chip key={a.ingredient_id} size="small" color="warning" variant="outlined" label={a.name ?? a.ingredient_id} />)
                : <Typography variant="body2" color="text.secondary">無</Typography>}</Box>
            </Box>
            <Button variant="outlined" className="live-member-edit" startIcon={<Pencil size={16} />}
              aria-label={`編輯 ${m.display_name}`} disabled={!options.data} component={Link} to={`/members/${m.id}/edit`}>編輯</Button>
          </CardContent>
        </Card>)}
      </Box>
      <Box className="live-family-actions">
        {members.data?.length === 0 && <Typography variant="body2" color="text.secondary">先新增一位成員，就能開始規劃餐食。</Typography>}
        <Button variant="contained" endIcon={<ArrowRight size={18} />} component={Link} to="/plan"
          disabled={members.isError || !members.data?.length}>開始規劃</Button>
      </Box>

      <Dialog open={!!deleting} onClose={() => setDeleting(null)}>
        <DialogTitle>刪除成員</DialogTitle>
        <DialogContent>
          <Typography>確定要刪除「{deleting?.display_name}」嗎？刪除後無法復原。</Typography>
          {remove.isError && <Alert severity="error" sx={{ mt: 2 }}>{errorMessage(remove.error)}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button color="inherit" onClick={() => setDeleting(null)}>取消</Button>
          <Button color="error" variant="contained" disabled={remove.isPending}
            onClick={() => deleting && remove.mutate(deleting.id)}>刪除</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
