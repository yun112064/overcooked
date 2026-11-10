import { Alert, Box, Button, Paper, Stack, Typography } from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ArrowRight, ArrowUpRight, CalendarDays, NotebookPen, Users, BookOpen } from 'lucide-react'
import { Link, useNavigate } from 'react-router'
import { api, errorMessage } from '../api/client'
import { MEAL_LABELS, type MealRequest, type MealRequestSummary, type Member } from '../api/types'

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function HomePage() {
  const navigate = useNavigate()
  const requests = useQuery({
    queryKey: ['meal-requests'],
    queryFn: async () => (await api.get<{ meal_requests: MealRequestSummary[] }>('/meal-requests')).data.meal_requests,
  })
  const members = useQuery({
    queryKey: ['members'],
    queryFn: async () => (await api.get<{ members: Member[] }>('/members')).data.members,
  })
  const create = useMutation({
    mutationFn: async () => (await api.post<MealRequest>('/meal-requests', { meal_date: today() })).data,
    onSuccess: (row) => navigate(`/plan/${row.id}`),
  })
  const draft = requests.data?.filter(row => row.status === 'draft' && row.mode === 'questionnaire')
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0]
  const resumePath = draft ? `/plan/${draft.id}` : '/plan'
  const start = () => {
    if (!members.data?.length) navigate('/members')
    else if (draft) navigate(resumePath)
    else create.mutate()
  }
  return (
    <Stack spacing={3}>
      <Box component="section" className="live-home-hero">
        <Box className="live-home-copy">
          <Typography className="live-eyebrow">為全家，好好準備一餐</Typography>
          <Typography component="h1">把家裡的食材，<br />變成全家的<span>好菜。</span></Typography>
          <Typography className="live-home-description">今天吃啥？讓我們一起想。<br />選好這餐的需求，再看看家裡有哪些食材。</Typography>
          <Button variant="contained" size="large" onClick={start} disabled={create.isPending || !members.data || !requests.data}
            endIcon={<ArrowRight size={18} />}>{create.isPending ? '建立中…' : '開始規劃'}</Button>
          <Box component="ol" className="live-home-steps"><li><span>1</span>當餐條件</li><li><span>2</span>現有食材</li><li><span>3</span>確認菜單</li></Box>
        </Box>
        <Box component="img" className="live-home-produce" src={`${import.meta.env.BASE_URL}produce-hero.webp`} alt="新鮮蔬果" />
      </Box>
      {(requests.isError || members.isError || create.isError) && <Alert severity="error">
        {errorMessage(requests.error ?? members.error ?? create.error)}</Alert>}
      <Box className="live-shortcuts">
        <Paper variant="outlined" className="live-shortcut">
          <Box className="live-shortcut-heading"><NotebookPen size={22} /><Typography component="h2">接續上次的規劃</Typography><span>草稿</span></Box>
          <Typography>{requests.isPending ? '正在讀取草稿…' : requests.isError ? '暫時無法讀取草稿' : draft
            ? `${draft.meal_date?.replaceAll('-', ' / ') ?? '未選日期'} · ${draft.meal_type ? MEAL_LABELS[draft.meal_type] : '未選餐別'}` : '尚無未完成的草稿'}</Typography>
          <Typography variant="body2" color="text.secondary">{draft ? '上次填寫的條件已保存在帳號中。' : '從一餐開始，安排家人的日常。'}</Typography>
          <Button component={Link} to={resumePath} endIcon={<ArrowUpRight size={17} />}>
            {draft ? '繼續草稿' : '查看餐食規劃'}</Button>
        </Paper>
        <Paper variant="outlined" className="live-shortcut">
          <Box className="live-shortcut-heading"><CalendarDays size={22} /><Typography component="h2">翻翻之前的餐食</Typography></Box>
          <Typography>從月曆選擇日期，<br />回看已採用的菜單、做法與採購資訊。</Typography>
          <Button component={Link} to="/history" endIcon={<ArrowUpRight size={17} />}>查看紀錄</Button>
        </Paper>
      </Box>
      <Box className="live-home-links">
        <Button component={Link} to="/members" startIcon={<Users size={18} />}>家庭成員</Button>
        <Button component={Link} to="/pick" startIcon={<BookOpen size={18} />}>從食譜庫選菜</Button>
      </Box>
    </Stack>
  )
}
