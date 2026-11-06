import ArrowBackOutlined from '@mui/icons-material/ArrowBackOutlined'
import {
  Alert, Box, Button, Checkbox, CircularProgress, Divider, FormControlLabel, MenuItem, Paper, Stack, Step, StepLabel, Stepper,
  TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { api, errorMessage } from '../api/client'
import {
  MEAL_LABELS, type MealRequest, type MealRequestDraft, type MealType, type Member, type Participant, type StapleOptions,
} from '../api/types'
import GenerationPanel from '../components/GenerationPanel'
import MenuPreviewPanel from '../components/MenuPreviewPanel'
import RecipeCatalogPicker from '../components/RecipeCatalogPicker'
import { PageHeader } from '../components/ui'
import { tokens } from '../theme'

const STEPS = ['用餐人數與便當', '挑選菜色', '結果']
const MEALS: MealType[] = ['breakfast', 'lunch', 'dinner']
type Picks = Record<string, { dining: boolean; bento: boolean }>

// 使用者本機時區的今天（YYYY-MM-DD）
function localToday() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** 依現在時間預設餐別（可改） */
function mealByClock(): MealType {
  const h = new Date().getHours()
  return h < 10 ? 'breakfast' : h < 15 ? 'lunch' : 'dinner'
}

/** 食譜庫選菜的草稿：不問預算、口味與已有食材 */
function pickerDraft(meal_type: MealType, picks: Picks, base?: MealRequestDraft): MealRequestDraft {
  const participants: Participant[] = Object.entries(picks).filter(([, p]) => p.dining || p.bento)
    .map(([member_id, p]) => ({ member_id, dining: p.dining, bento: p.bento, bento_meal: 'lunch' }))
  return {
    mode: 'picker', meal_date: base?.meal_date ?? localToday(), meal_type, budget: null,
    staple: base?.staple ?? null, dish_count: base?.dish_count ?? null, taste_preferences: '',
    specified_dishes: base?.specified_dishes ?? [], participants, available_ingredient_ids: [],
  }
}

/** 食譜庫選菜（與問卷分開的功能）：選誰吃飯、誰帶便當 → 從食譜庫挑菜 → 依人數調整份量生成，結果頁與問卷共用。 */
export default function RecipePickerPage() {
  const { id } = useParams()
  const request = useQuery({
    queryKey: ['meal-request', id],
    queryFn: async () => (await api.get<MealRequest>(`/meal-requests/${id}`)).data,
    enabled: !!id,
  })
  const members = useQuery({
    queryKey: ['members'],
    queryFn: async () => (await api.get<{ members: Member[] }>('/members')).data.members,
  })
  if (request.isError) return <Alert severity="error">{errorMessage(request.error)}</Alert>
  if (members.isError) return <Alert severity="error">{errorMessage(members.error)}</Alert>
  if ((id && !request.data) || !members.data) return <CircularProgress />
  if (request.data && request.data.draft.mode !== 'picker') return <Navigate to={`/plan/${id}`} replace />
  // key：新建後換成已存的那份時重建狀態
  return <Picker key={request.data?.id ?? 'new'} initial={request.data ?? null} members={members.data} />
}

function Picker({ initial, members }: { initial: MealRequest | null; members: Member[] }) {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [server, setServer] = useState<MealRequest | null>(initial)
  const [step, setStep] = useState(() => {
    if (!initial) return 0
    if (initial.status !== 'draft') return 2
    return (location.state as { step?: number } | null)?.step ?? 0
  })
  const [autoSubmit, setAutoSubmit] = useState(false)
  const [mealType, setMealType] = useState<MealType>(initial?.draft.meal_type ?? mealByClock())
  const [picks, setPicks] = useState<Picks>(() => {
    const byId = new Map(initial?.draft.participants.map((p) => [p.member_id, p]))
    return Object.fromEntries(members.map((m) => [m.id, { dining: byId.get(m.id)?.dining ?? false, bento: byId.get(m.id)?.bento ?? false }]))
  })

  const staples = useQuery({
    queryKey: ['staple-options'],
    queryFn: async () => (await api.get<StapleOptions>('/meal-requests/staple-options')).data,
    staleTime: Infinity,
  })

  const save = useMutation({
    mutationFn: async (draft: MealRequestDraft) => server
      ? (await api.put<MealRequest>(`/meal-requests/${server.id}`, { version: server.version, draft })).data
      : (await api.post<MealRequest>('/meal-requests', draft)).data,
    onSuccess: (row) => {
      setServer(row)
      queryClient.setQueryData(['meal-request', row.id], row)
      queryClient.invalidateQueries({ queryKey: ['meal-requests'] })
    },
  })

  const locked = server?.status === 'queued' || server?.status === 'running'
  const adopted = server?.status === 'confirmed'
  const readOnly = locked || adopted
  const dining = Object.values(picks).filter((p) => p.dining).length
  const bento = Object.values(picks).filter((p) => p.bento).length

  const savePeople = async () => {
    const draft = pickerDraft(mealType, picks, server?.draft)
    if (server && readOnly) { setStep(1); return }
    try {
      const row = await save.mutateAsync(draft)
      // 第一次儲存才建立問卷：換到它的網址，之後重新整理也回得來
      if (!server) navigate(`/pick/${row.id}`, { replace: true, state: { step: 1 } })
      else setStep(1)
    } catch { /* 錯誤訊息已顯示 */ }
  }

  // 所選的菜就是整餐的菜（菜數跟著改）；沒挑飯麵類時配一份清單第一個主食（白飯），挑了就不另配
  const confirmDishes = async (names: string[], hasStaple: boolean) => {
    if (!server || !staples.data) return
    const staple = hasStaple ? staples.data.none_value : (staples.data.options[0]?.label ?? staples.data.none_value)
    try {
      await save.mutateAsync({ ...pickerDraft(mealType, picks, server.draft), specified_dishes: names, dish_count: names.length, staple })
      setStep(2)
      setAutoSubmit(true)
    } catch { /* 錯誤訊息已顯示 */ }
  }

  const refresh = async () => {
    if (!server) return
    const row = (await api.get<MealRequest>(`/meal-requests/${server.id}`)).data
    setServer(row)
  }

  const goBack = (to: number) => { setAutoSubmit(false); setStep(to) }

  return (
    <Stack spacing={3}>
      <Box>
        <Button component={Link} to="/plan" size="small" color="inherit" startIcon={<ArrowBackOutlined />}
          sx={{ color: tokens.inkSoft, ml: -1.5, mb: 1 }}>
          規劃餐食
        </Button>
        <PageHeader eyebrow={`食譜庫選菜・Step ${step + 1} / ${STEPS.length}`} title={STEPS[step]} />
      </Box>

      <Stepper activeStep={step} alternativeLabel>
        {STEPS.map((label, i) => (
          <Step key={label} completed={i < step}>
            <StepLabel onClick={() => i < step && goBack(i)} sx={{ cursor: i < step ? 'pointer' : 'default' }}>{label}</StepLabel>
          </Step>
        ))}
      </Stepper>

      {save.isError && <Alert severity="error">{errorMessage(save.error)}</Alert>}
      {locked && <Alert severity="info">正在生成中，完成前無法修改。</Alert>}
      {adopted && <Alert severity="info">這餐已採用，只能瀏覽；想換菜請重新挑一次。</Alert>}

      {step === 0 && (
        <Paper variant="outlined" sx={{ p: { xs: 2.5, sm: 4 } }}>
          <Stack spacing={2}>
            {members.length === 0 && <Alert severity="info">還沒有家庭成員。<Link to="/members">先新增成員</Link></Alert>}
            <Typography variant="body2" color="text.secondary">
              勾選這餐要一起吃的人與要帶便當的人；份量會依每個人的食量自動調整。
            </Typography>
            <TextField select size="small" label="這一餐" value={mealType} disabled={readOnly}
              onChange={(e) => setMealType(e.target.value as MealType)} sx={{ maxWidth: 200 }}>
              {MEALS.map((m) => <MenuItem key={m} value={m}>{MEAL_LABELS[m]}</MenuItem>)}
            </TextField>
            {members.map((m) => {
              const p = picks[m.id]
              const on = p.dining || p.bento
              const set = (patch: Partial<typeof p>) => setPicks((all) => ({ ...all, [m.id]: { ...all[m.id], ...patch } }))
              return (
                <Paper key={m.id} variant="outlined" sx={{ px: 2, py: 1, display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap',
                  bgcolor: on ? '#FFFDF9' : 'transparent', borderColor: on ? tokens.lineStrong : tokens.line }}>
                  <Typography sx={{ fontFamily: tokens.serif, mr: 'auto' }}>
                    {m.display_name}<Typography component="span" variant="caption" sx={{ ml: 1 }}>食量 {m.portion}n</Typography>
                  </Typography>
                  <FormControlLabel label="一起吃" disabled={readOnly}
                    control={<Checkbox checked={p.dining} onChange={(e) => set({ dining: e.target.checked })} />} />
                  <FormControlLabel label="帶便當" disabled={readOnly}
                    control={<Checkbox checked={p.bento} onChange={(e) => set({ bento: e.target.checked })} />} />
                </Paper>
              )
            })}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
              <Typography variant="body2" sx={{ mr: 'auto' }}>用餐 {dining} 人・便當 {bento} 個</Typography>
              <Button variant="contained" disabled={save.isPending || dining + bento === 0} onClick={savePeople}>
                下一步：挑菜
              </Button>
            </Box>
          </Stack>
        </Paper>
      )}

      {step === 1 && server && (
        <Stack spacing={1}>
          <Typography variant="caption">
            沒挑飯、麵類的菜時會配一份{staples.data?.options[0]?.label ?? '白飯'}；按確定後依人數調整份量並開始生成。
          </Typography>
          <RecipeCatalogPicker requestId={server.id} initial={server.draft.specified_dishes}
            disabled={readOnly || save.isPending || !staples.data} onConfirm={confirmDishes} />
        </Stack>
      )}

      {step === 2 && server && (
        <Paper variant="outlined" sx={{ p: { xs: 2.5, sm: 4 } }}>
          <Stack spacing={2}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
              <Typography variant="body2" sx={{ mr: 'auto' }}>
                {server.draft.meal_type ? MEAL_LABELS[server.draft.meal_type] : ''}・用餐 {dining} 人・便當 {bento} 個・
                {server.draft.specified_dishes.join('、')}
              </Typography>
              {!readOnly && <Button size="small" onClick={() => goBack(1)}>重新挑菜</Button>}
            </Box>
            {!server.complete && !readOnly && (
              <Alert severity="warning">{server.issues.map((i) => i.message).join(' ')}</Alert>
            )}
            {/* 與問卷最後一頁相同：資料庫菜單與營養（T11／T12），再由生成結果補上價格（T13／T14） */}
            <MenuPreviewPanel row={server} members={members} />
            <Divider />
            <GenerationPanel row={server} onChanged={refresh} autoSubmit={autoSubmit} />
          </Stack>
        </Paper>
      )}
    </Stack>
  )
}
