import AddOutlined from '@mui/icons-material/AddOutlined'
import DeleteOutlineOutlined from '@mui/icons-material/DeleteOutlineOutlined'
import EditOutlined from '@mui/icons-material/EditOutlined'
import GroupOutlined from '@mui/icons-material/GroupOutlined'
import {
  Alert, Box, Button, Card, CardContent, Chip, CircularProgress, Dialog, DialogActions,
  DialogContent, DialogTitle, IconButton, Stack, Tooltip, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, errorMessage } from '../api/client'
import { ACTIVITY_LABELS, GOAL_LABELS, type Member, type MemberOptions } from '../api/types'
import MemberFormDialog from '../components/MemberFormDialog'
import { EmptyState, PageHeader } from '../components/ui'
import { tokens } from '../theme'

/** 頭像字：中文名去掉姓氏取第二個字（林媽媽→媽、小宇→宇），其他取第一個字母。 */
function initial(name: string) {
  const n = name.trim()
  return /^\p{Script=Han}/u.test(n) && n.length >= 2 ? n[1] : n.slice(0, 1).toUpperCase()
}

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

  const addButton = (
    <Button variant="contained" startIcon={<AddOutlined />} onClick={() => openForm(null)} disabled={!options.data}>
      新增成員
    </Button>
  )

  return (
    <Stack spacing={4}>
      <PageHeader eyebrow="Family" title="家庭成員"
        description="每位成員的年齡、體重、活動量與目標，決定每個人這一餐的熱量與蛋白質；過敏和不吃的食材一定會避開。"
        action={members.data && members.data.length > 0 ? addButton : undefined} />

      {(members.isPending || options.isPending) && <CircularProgress />}
      {members.isError && <Alert severity="error">{errorMessage(members.error)}</Alert>}
      {options.isError && <Alert severity="error">{errorMessage(options.error)}</Alert>}
      {members.data?.length === 0 && (
        <EmptyState icon={<GroupOutlined />} title="先認識一下你的家人"
          description="新增成員後，規劃餐食時就能選擇誰要用餐，系統會依每個人的需要分配份量。"
          action={addButton} />
      )}

      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, minmax(0, 1fr))' } }}>
        {members.data?.map((m) => (
          <Card key={m.id}>
            <CardContent>
              <Box sx={{ display: 'flex', gap: 2, alignItems: 'flex-start' }}>
                <Box aria-hidden sx={{
                  width: 44, height: 44, borderRadius: '50%', flexShrink: 0, display: 'grid', placeItems: 'center',
                  bgcolor: m.equation_sex === 'F' ? '#F1E4DF' : '#E3E8EF',
                  color: m.equation_sex === 'F' ? tokens.brick : tokens.indigoDark,
                  fontFamily: tokens.serif, fontSize: '1.1rem',
                }}>
                  {initial(m.display_name)}
                </Box>
                <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                  <Typography variant="h6" component="h2" sx={{ lineHeight: 1.4 }}>{m.display_name}</Typography>
                  <Typography color="text.secondary" variant="body2">
                    {m.age_years} 歲・{m.equation_sex === 'F' ? '女' : '男'}・{m.weight_kg} 公斤
                  </Typography>
                  <Typography color="text.secondary" variant="caption">{ACTIVITY_LABELS[m.activity_category]}</Typography>
                </Box>
                <Box sx={{ display: 'flex', mt: -0.5, mr: -1 }}>
                  <Tooltip title="編輯">
                    <span>
                      <IconButton size="small" aria-label={`編輯 ${m.display_name}`} onClick={() => openForm(m)}
                        disabled={!options.data}><EditOutlined fontSize="small" /></IconButton>
                    </span>
                  </Tooltip>
                  <Tooltip title="刪除">
                    <IconButton size="small" aria-label={`刪除 ${m.display_name}`} onClick={() => setDeleting(m)}
                      sx={{ '&:hover': { color: tokens.brick } }}><DeleteOutlineOutlined fontSize="small" /></IconButton>
                  </Tooltip>
                </Box>
              </Box>
              <Stack direction="row" spacing={1} sx={{ mt: 2, pt: 2, borderTop: `1px solid ${tokens.line}`, flexWrap: 'wrap', rowGap: 1 }}>
                <Chip size="small" label={GOAL_LABELS[m.goal]} />
                <Chip size="small" label={`食量 ${m.portion}n`} />
                {m.allergens.map((a) => (
                  <Chip key={a.ingredient_id} size="small" color="error" variant="outlined" label={`過敏・${a.name}`} />
                ))}
                {m.avoided.map((a) => (
                  <Chip key={a.ingredient_id} size="small" color="warning" variant="outlined" label={`不吃・${a.name}`} />
                ))}
              </Stack>
            </CardContent>
          </Card>
        ))}
      </Box>

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
          <Button color="inherit" onClick={() => setDeleting(null)}>取消</Button>
          <Button color="error" variant="contained" disabled={remove.isPending}
            onClick={() => deleting && remove.mutate(deleting.id)}>刪除</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
