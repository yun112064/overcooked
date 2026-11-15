import { Alert, Button, CircularProgress, Stack, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useOutletContext, useParams } from 'react-router'
import { api, errorMessage } from '../api/client'
import type { Me, Member, MemberOptions } from '../api/types'
import MemberForm from '../components/MemberForm'

export default function MemberEditorPage() {
  const { current_household: household } = useOutletContext<Me>()
  const { memberId } = useParams()
  const navigate = useNavigate()
  // 每次進入編輯頁重新載入；不把上一頁或別的家庭的快取當成編輯基準。
  const members = useQuery({
    queryKey: ['members', household?.id, 'editor', memberId],
    queryFn: async () => (await api.get<{ members: Member[] }>('/members')).data.members,
    enabled: !!memberId,
    gcTime: 0,
  })
  const options = useQuery({
    queryKey: ['member-options'],
    queryFn: async () => (await api.get<MemberOptions>('/members/options')).data,
    staleTime: Infinity,
  })
  const error = options.error ?? (memberId ? members.error : null)
  if (error) return <Alert severity="error" action={<Button color="inherit" onClick={() => {
    void options.refetch()
    if (memberId) void members.refetch()
  }}>重試</Button>}>{errorMessage(error)}</Alert>
  if (!options.data || (memberId && members.isPending)) return <CircularProgress aria-label="正在讀取成員資料" />
  const member = memberId ? members.data?.find(item => item.id === memberId) : null
  if (memberId && !member) return <Stack spacing={2}>
    <Typography>找不到此成員，或此成員不屬於目前家庭。</Typography>
    <Button component={Link} to="/members">返回家庭成員</Button>
  </Stack>
  return <MemberForm key={`${household?.id}/${memberId ?? 'new'}`} member={member ?? null}
    options={options.data} onClose={() => navigate('/members')} />
}
