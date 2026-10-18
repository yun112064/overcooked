import { zodResolver } from '@hookform/resolvers/zod'
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, CircularProgress, Divider, FormControlLabel, MenuItem,
  Paper, Stack, Step, StepLabel, Stepper, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Controller, useForm, useWatch, type FieldPath } from 'react-hook-form'
import { Link, useParams } from 'react-router'
import { z } from 'zod'
import { api, apiError, errorMessage, errorStatus } from '../api/client'
import {
  MEAL_LABELS, type IngredientRef, type MealRequest, type MealRequestDraft, type MealType, type Member,
} from '../api/types'
import FormTextField from '../components/FormTextField'
import GenerationPanel from '../components/GenerationPanel'
import IngredientPicker from '../components/IngredientPicker'
import RecognitionCapture from '../components/RecognitionCapture'

const STEPS = ['用餐資訊', '成員與便當', '口味與指定菜', '已有食材', '確認']
const MEALS: MealType[] = ['breakfast', 'lunch', 'dinner']
const STAPLE_SUGGESTIONS = ['白飯', '糙米飯', '麵', '麵包', '地瓜', '不需要主食']

const participantSchema = z.object({ dining: z.boolean(), bento: z.boolean(), bento_meal: z.enum(['breakfast', 'lunch', 'dinner']) })

const schema = z.object({
  meal_date: z.string(),
  meal_type: z.union([z.enum(['breakfast', 'lunch', 'dinner']), z.literal('')]),
  budget: z.string().trim().refine(
    (v) => v === '' || (/^\d{1,6}(\.\d{1,2})?$/.test(v) && Number(v) > 0 && Number(v) <= 100000),
    '預算需為大於 0 的金額（最多兩位小數）',
  ),
  staple: z.string().max(30, '最多 30 個字'),
  dish_count: z.string(),
  taste_preferences: z.string().max(200, '最多 200 個字'),
  specified_dishes: z.array(z.string().trim().min(1).max(50, '每道菜名最多 50 個字')).max(10, '最多指定 10 道'),
  participants: z.record(z.string(), participantSchema),
  available: z.array(z.object({ ingredient_id: z.string(), name: z.string().nullable() })),
})
type FormValues = z.infer<typeof schema>

const STEP_FIELDS: FieldPath<FormValues>[][] = [
  ['meal_date', 'meal_type', 'budget', 'staple', 'dish_count'],
  ['participants'],
  ['taste_preferences', 'specified_dishes'],
  ['available'],
  [],
]

function toForm(row: MealRequest, members: Member[]): FormValues {
  const d = row.draft
  const byId = new Map(d.participants.map((p) => [p.member_id, p]))
  return {
    meal_date: d.meal_date ?? '',
    meal_type: d.meal_type ?? '',
    budget: d.budget ?? '',
    staple: d.staple ?? '',
    dish_count: d.dish_count ? String(d.dish_count) : '',
    taste_preferences: d.taste_preferences,
    specified_dishes: d.specified_dishes,
    participants: Object.fromEntries(members.map((m) => {
      const p = byId.get(m.id)
      return [m.id, { dining: p?.dining ?? false, bento: p?.bento ?? false, bento_meal: p?.bento_meal ?? 'lunch' }]
    })),
    available: row.available_ingredients,
  }
}

function toDraft(v: FormValues): MealRequestDraft {
  return {
    meal_date: v.meal_date || null,
    meal_type: v.meal_type || null,
    budget: v.budget.trim() || null,
    staple: v.staple.trim() || null,
    dish_count: v.dish_count ? Number(v.dish_count) : null,
    taste_preferences: v.taste_preferences.trim(),
    specified_dishes: v.specified_dishes.map((s) => s.trim()).filter(Boolean),
    participants: Object.entries(v.participants)
      .filter(([, p]) => p.dining || p.bento)
      .map(([member_id, p]) => ({ member_id, ...p })),
    available_ingredient_ids: v.available.map((i) => i.ingredient_id),
  }
}

function fmt(value: string, digits = 1) {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(digits) : value
}

export default function MealRequestEditor() {
  const { id } = useParams()
  const request = useQuery({
    queryKey: ['meal-request', id],
    queryFn: async () => (await api.get<MealRequest>(`/meal-requests/${id}`)).data,
  })
  const members = useQuery({
    queryKey: ['members'],
    queryFn: async () => (await api.get<{ members: Member[] }>('/members')).data.members,
  })
  if (request.isError) return <Alert severity="error">{errorMessage(request.error)}</Alert>
  if (members.isError) return <Alert severity="error">{errorMessage(members.error)}</Alert>
  if (!request.data || !members.data) return <CircularProgress />
  // key：換問卷時重建表單狀態
  return <Editor key={request.data.id} initial={request.data} members={members.data} />
}

function Editor({ initial, members: memberList }: { initial: MealRequest; members: Member[] }) {
  const id = initial.id
  const queryClient = useQueryClient()
  const [step, setStep] = useState(0)
  const [server, setServer] = useState<MealRequest>(initial)

  const form = useForm<FormValues>({ mode: 'onTouched', resolver: zodResolver(schema), defaultValues: toForm(initial, memberList) })
  const { control, register, trigger, getValues, setError, formState: { errors } } = form

  const save = useMutation({
    mutationFn: async () =>
      (await api.put<MealRequest>(`/meal-requests/${id}`, { version: server.version, draft: toDraft(getValues()) })).data,
    onSuccess: (row) => {
      setServer(row)
      queryClient.setQueryData(['meal-request', id], row)
      queryClient.invalidateQueries({ queryKey: ['meal-requests'] })
    },
    onError: (err) => {
      const e = apiError(err)
      const fields = e?.details?.fields ?? (e?.details?.field ? [{ field: e.details.field, message: e.message }] : [])
      for (const f of fields) {
        const name = f.field.replace(/^draft\./, '').split('.')[0]
        const key = name === 'available_ingredient_ids' ? 'available' : name
        if (key in getValues()) setError(key as FieldPath<FormValues>, { message: f.message })
      }
    },
  })

  // 送出後重新讀取問卷狀態（queued／running 時不能修改）
  const refresh = async () => {
    const row = (await api.get<MealRequest>(`/meal-requests/${id}`)).data
    setServer(row)
    queryClient.setQueryData(['meal-request', id], row)
  }
  const locked = server.status === 'queued' || server.status === 'running'

  const goTo = async (next: number) => {
    // 生成中只能瀏覽，不儲存
    if (locked) { setStep(next); return }
    // 只檢查格式；未填完也能前往下一步，草稿不會遺失
    if (!(await trigger(STEP_FIELDS[step]))) return
    try {
      await save.mutateAsync()
      setStep(next)
    } catch {
      /* 錯誤訊息已顯示 */
    }
  }

  const dishCount = useWatch({ control, name: 'dish_count' })
  const specified = useWatch({ control, name: 'specified_dishes' })

  const conflict = save.isError && errorStatus(save.error) === 409

  return (
    <Stack spacing={3}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
        <Typography variant="h5" component="h1" sx={{ flexGrow: 1 }}>當餐問卷</Typography>
        <Typography variant="body2" color="text.secondary">
          草稿版本 {server.version}{save.isPending ? '・儲存中…' : ''}
        </Typography>
      </Box>

      <Stepper activeStep={step} alternativeLabel sx={{ overflowX: 'auto', pb: 1 }}>
        {STEPS.map((label, i) => (
          <Step key={label} completed={i < step}><StepLabel>{label}</StepLabel></Step>
        ))}
      </Stepper>

      {conflict && (
        <Alert severity="warning" action={<Button color="inherit" onClick={() => window.location.reload()}>重新載入</Button>}>
          {errorMessage(save.error)}
        </Alert>
      )}
      {save.isError && !conflict && <Alert severity="error">{errorMessage(save.error)}</Alert>}
      {locked && <Alert severity="info">此問卷正在生成中，完成前無法修改。</Alert>}

      <Paper variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
        {step === 0 && (
          <Stack spacing={2}>
            <TextField label="餐食日期" type="date" {...register('meal_date')}
              slotProps={{ inputLabel: { shrink: true } }} error={!!errors.meal_date} helperText={errors.meal_date?.message} />
            <Controller name="meal_type" control={control} render={({ field }) => (
              <TextField select label="餐別" {...field} helperText="早餐 30%、午餐 30%、晚餐 40% 的每日目標">
                <MenuItem value="">未選擇</MenuItem>
                {MEALS.map((m) => <MenuItem key={m} value={m}>{MEAL_LABELS[m]}</MenuItem>)}
              </TextField>
            )} />
            <FormTextField name="budget" control={control} label="預算（新台幣）" inputMode="decimal"
              helperText="以需補買食材的比例成本判斷是否在預算內" />
            <Controller name="staple" control={control} render={({ field }) => (
              <Autocomplete freeSolo options={STAPLE_SUGGESTIONS} value={field.value}
                onChange={(_, v) => field.onChange(v ?? '')} onInputChange={(_, v) => field.onChange(v)}
                renderInput={(params) => <TextField {...params} label="主食" error={!!errors.staple} helperText={errors.staple?.message} />} />
            )} />
            <Controller name="dish_count" control={control} render={({ field }) => (
              <TextField select label="菜數" {...field} helperText="指定菜色也會計入菜數">
                <MenuItem value="">未選擇</MenuItem>
                {Array.from({ length: 10 }, (_, i) => String(i + 1)).map((n) => <MenuItem key={n} value={n}>{n} 道</MenuItem>)}
              </TextField>
            )} />
          </Stack>
        )}

        {step === 1 && (
          <Stack spacing={2}>
            {memberList.length === 0 && (
              <Alert severity="info">還沒有家庭成員。<Link to="/members">先新增成員</Link></Alert>
            )}
            <Typography variant="body2" color="text.secondary">
              勾選這餐要一起吃的成員；帶便當代表另外多準備一份，同一人可同時勾選。
            </Typography>
            {memberList.map((m) => (
              <Controller key={m.id} name={`participants.${m.id}`} control={control} render={({ field }) => (
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography sx={{ fontWeight: 500 }}>{m.display_name}（{m.portion}n）</Typography>
                  <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ alignItems: { sm: 'center' } }}>
                    <FormControlLabel label="一起用餐" control={
                      <Checkbox checked={field.value?.dining ?? false}
                        onChange={(e) => field.onChange({ ...field.value, dining: e.target.checked })} />} />
                    <FormControlLabel label="帶便當" control={
                      <Checkbox checked={field.value?.bento ?? false}
                        onChange={(e) => field.onChange({ ...field.value, bento: e.target.checked })} />} />
                    {field.value?.bento && (
                      <TextField select size="small" label="便當餐別" value={field.value.bento_meal}
                        onChange={(e) => field.onChange({ ...field.value, bento_meal: e.target.value })} sx={{ minWidth: 140 }}>
                        {MEALS.map((meal) => <MenuItem key={meal} value={meal}>{MEAL_LABELS[meal]}</MenuItem>)}
                      </TextField>
                    )}
                  </Stack>
                </Paper>
              )} />
            ))}
          </Stack>
        )}

        {step === 2 && (
          <Stack spacing={2}>
            <FormTextField name="taste_preferences" control={control} label="口味偏好" multiline minRows={2}
              helperText="例如：清淡、少辣（會盡量滿足）" />
            <Controller name="specified_dishes" control={control} render={({ field }) => (
              <Autocomplete multiple freeSolo options={[]} value={field.value}
                onChange={(_, v) => field.onChange(v.map((s) => String(s).trim()).filter(Boolean))}
                renderInput={(params) => (
                  <TextField {...params} label="指定菜色" error={!!errors.specified_dishes}
                    helperText={errors.specified_dishes?.message
                      ?? `輸入菜名後按 Enter；指定菜一定會出現在菜單中${dishCount ? `（已指定 ${specified?.length ?? 0}／${dishCount} 道）` : ''}`} />
                )} />
            )} />
          </Stack>
        )}

        {step === 3 && (
          <Stack spacing={2}>
            <Typography variant="body2" color="text.secondary">
              選擇家中已有的食材種類。已有的種類視為足夠這餐使用，不需要輸入重量；沒選的食材會按整餐需求量補買。
            </Typography>
            <RecognitionCapture mealRequestId={id} onAdd={(ingredient) => {
              const current = getValues('available')
              if (!current.some((i) => i.ingredient_id === ingredient.ingredient_id)) {
                form.setValue('available', [...current, ingredient], { shouldDirty: true })
              }
            }} />
            <Controller name="available" control={control} render={({ field }) => (
              <IngredientPicker label="家中已有的食材（也可直接搜尋加入，例如肉類）" value={field.value as IngredientRef[]}
                onChange={field.onChange} error={!!errors.available} helperText={errors.available?.message} />
            )} />
          </Stack>
        )}

        {step === 4 && (
          <Stack spacing={3}>
            <Summary row={server} members={memberList} />
            <Divider />
            <GenerationPanel row={server} onChanged={refresh} />
          </Stack>
        )}
      </Paper>

      <Box sx={{ display: 'flex', gap: 1, justifyContent: 'space-between', flexWrap: 'wrap' }}>
        <Button disabled={step === 0 || save.isPending} onClick={() => goTo(step - 1)}>上一步</Button>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button variant="outlined" disabled={save.isPending || locked} onClick={() => goTo(step)}>儲存草稿</Button>
          {step < STEPS.length - 1 && (
            <Button variant="contained" disabled={save.isPending} onClick={() => goTo(step + 1)}>下一步</Button>
          )}
        </Box>
      </Box>
    </Stack>
  )
}

function Summary({ row, members }: { row: MealRequest; members: Member[] }) {
  const names = new Map(members.map((m) => [m.id, m.display_name]))
  const d = row.draft
  const preview = row.targets_preview
  return (
    <Stack spacing={2}>
      {row.complete
        ? <Alert severity="success">問卷已填寫完整，可以送出生成。</Alert>
        : (
          <Alert severity="warning">
            還有項目需要補充：
            <Box component="ul" sx={{ m: 0, pl: 3 }}>{row.issues.map((i) => <li key={i.field + i.reason}>{i.message}</li>)}</Box>
          </Alert>
        )}
      <Typography>
        {d.meal_date ?? '未選日期'}・{d.meal_type ? MEAL_LABELS[d.meal_type] : '未選餐別'}・
        預算 {d.budget ?? '未填'} 元・主食 {d.staple ?? '未填'}・{d.dish_count ?? '?'} 道菜
      </Typography>
      {d.specified_dishes.length > 0 && <Typography>指定菜色：{d.specified_dishes.join('、')}</Typography>}
      {d.taste_preferences && <Typography>口味：{d.taste_preferences}</Typography>}

      <Divider />
      <Typography variant="subtitle1" component="h2">製作項目</Typography>
      {row.items.length === 0 && <Typography color="text.secondary">尚未選擇成員</Typography>}
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
        {row.items.map((i) => (
          <Chip key={i.participation_id}
            label={`${names.get(i.member_id) ?? '已刪除成員'}・${i.kind === 'bento' ? '便當' : '用餐'}（${MEAL_LABELS[i.meal]}）`} />
        ))}
      </Stack>

      {preview.status === 'available' && (
        <Typography>
          本餐全家目標（預覽）：約 {fmt(preview.target.energy_kcal, 0)} 大卡、蛋白質 {fmt(preview.target.protein_g)} 公克，
          合計 {preview.total_n}n
        </Typography>
      )}
      {preview.status === 'unavailable' && <Alert severity="warning">{preview.message}</Alert>}

      <Divider />
      <Typography variant="subtitle1" component="h2">必要限制（參與成員聯集）</Typography>
      {row.restrictions.ingredients.length === 0 && <Typography color="text.secondary">無過敏或禁吃食材</Typography>}
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 1 }}>
        {row.restrictions.ingredients.map((r) => (
          <Chip key={r.ingredient_id} color={r.kinds.includes('allergen') ? 'error' : 'warning'} variant="outlined"
            label={`${r.name}：${r.kinds.includes('allergen') ? '過敏' : '禁吃'}（${r.member_names.join('、')}）`} />
        ))}
      </Stack>
      {row.restrictions.available_but_restricted.length > 0 && (
        <Alert severity="info">家中已有的食材中，有成員過敏或禁吃的項目；產生菜單時仍會排除這些食材。</Alert>
      )}

      <Divider />
      <Typography variant="subtitle1" component="h2">家中已有食材</Typography>
      {row.available_ingredients.length === 0
        ? <Typography color="text.secondary">未選擇（全部按需求量補買）</Typography>
        : <Typography>{row.available_ingredients.map((i) => i.name).join('、')}</Typography>}
    </Stack>
  )
}
