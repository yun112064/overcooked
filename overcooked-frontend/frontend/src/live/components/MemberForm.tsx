import { zodResolver } from '@hookform/resolvers/zod'
import {
  Alert, Box, Button, Card, CardContent, Dialog, DialogActions, DialogContent, DialogTitle, FormControl, FormControlLabel, FormHelperText, FormLabel, IconButton, MenuItem, Radio, RadioGroup, Stack, TextField, Tooltip, Typography,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useBeforeUnload, useBlocker } from 'react-router'
import { ArrowLeft, Check, CircleHelp, ShieldCheck, UserPlus, UserRound, Utensils } from 'lucide-react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { z } from 'zod'
import { api, apiError } from '../api/client'
import {
  ACTIVITY_LABELS, GOAL_LABELS, type Activity, type Goal, type Member, type MemberInput, type MemberOptions,
} from '../api/types'
import FormTextField from './FormTextField'
import IngredientPicker from './IngredientPicker'

const PORTION_HINT: Record<string, string> = {
  '0.7': '0.7n（食量較小）',
  '0.85': '0.85n',
  '1': '1n（標準成人一份）',
  '1.15': '1.15n',
  '1.3': '1.3n（食量較大）',
}

const today = () => new Date().toISOString().slice(0, 10)

function ageOn(birth: string, on: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(birth)) return null
  const [by, bm, bd] = birth.split('-').map(Number)
  const [ty, tm, td] = on.split('-').map(Number)
  return ty - by - (tm < bm || (tm === bm && td < bd) ? 1 : 0)
}

function buildSchema(options: MemberOptions) {
  const ingredient = z.object({ ingredient_id: z.string(), name: z.string().nullable() })
  return z.object({
    display_name: z.string().trim().min(1, '請輸入成員名稱').max(50, '最多 50 個字'),
    birth_date: z.string()
      .regex(/^\d{4}-\d{2}-\d{2}$/, '請選擇出生日期')
      .refine((v) => v <= today(), '出生日期不能晚於今天'),
    equation_sex: z.enum(['M', 'F'], { error: '請選擇公式性別' }),
    weight_kg: z.string().trim()
      .regex(/^\d{1,3}(\.\d{1,2})?$/, '請輸入體重（公斤，最多兩位小數）')
      .refine((v) => Number(v) > 0 && Number(v) <= 500, '體重需大於 0 且不超過 500'),
    activity_category: z.enum(options.activity_categories as [Activity, ...Activity[]]),
    goal: z.enum(options.goals as [Goal, ...Goal[]]),
    portion: z.enum(options.portions as [string, ...string[]], { error: '請選擇食量' }),
    allergens: z.array(ingredient),
    avoided: z.array(ingredient),
  }).superRefine((v, ctx) => {
    const age = ageOn(v.birth_date, today())
    if (age !== null && age < options.adult_goals_min_age_years && !options.minor_goals.includes(v.goal)) {
      ctx.addIssue({ code: 'custom', path: ['goal'], message: '未滿 19 歲只能選擇一般餐食' })
    }
  })
}

type FormValues = z.infer<ReturnType<typeof buildSchema>>

const FIELD_MAP: Record<string, keyof FormValues> = {
  allergen_ingredient_ids: 'allergens',
  avoided_ingredient_ids: 'avoided',
}

type Props = {
  member: Member | null
  options: MemberOptions
  onClose: () => void
}

function toForm(member: Member | null): FormValues {
  return member
    ? {
        display_name: member.display_name,
        birth_date: member.birth_date,
        equation_sex: member.equation_sex,
        weight_kg: member.weight_kg,
        activity_category: member.activity_category,
        goal: member.goal,
        portion: member.portion,
        allergens: member.allergens,
        avoided: member.avoided,
      }
    : {
        display_name: '', birth_date: '', equation_sex: 'F', weight_kg: '',
        activity_category: 'low_active', goal: 'general', portion: '1', allergens: [], avoided: [],
      }
}

export default function MemberForm({ member: initialMember, options, onClose }: Props) {
  // 固定開始編輯時的版本；背景更新不能替舊表單換上新的版本號。
  const [member] = useState(initialMember)
  const allowLeave = useRef(false)
  const queryClient = useQueryClient()
  const { control, handleSubmit, setError, setValue, getValues, formState: { errors, isDirty } } =
    useForm<FormValues>({ mode: 'onTouched', resolver: zodResolver(buildSchema(options)), defaultValues: toForm(member) })


  const birthDate = useWatch({ control, name: 'birth_date' })
  const age = ageOn(birthDate ?? '', today())
  const isMinor = age !== null && age < options.adult_goals_min_age_years
  useEffect(() => {
    if (isMinor && !options.minor_goals.includes(getValues('goal'))) setValue('goal', 'general')
  }, [isMinor, options.minor_goals, getValues, setValue])

  const save = useMutation({
    mutationFn: async (values: FormValues) => {
      const body: MemberInput = {
        display_name: values.display_name,
        birth_date: values.birth_date,
        equation_sex: values.equation_sex,
        weight_kg: values.weight_kg,
        activity_category: values.activity_category,
        goal: values.goal,
        portion: values.portion,
        allergen_ingredient_ids: values.allergens.map((i) => i.ingredient_id),
        avoided_ingredient_ids: values.avoided.map((i) => i.ingredient_id),
      }
      return member
        ? (await api.put<Member>(`/members/${member.id}`, { ...body, version: member.version })).data
        : (await api.post<Member>('/members', body)).data
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['members'] })
      allowLeave.current = true
      onClose()
    },
    onError: (err) => {
      // 後端是最終驗證者：把欄位錯誤顯示在對應欄位
      const e = apiError(err)
      const field = e?.details?.field
      const key = field ? (FIELD_MAP[field] ?? field) : undefined
      if (key && key in toForm(null)) setError(key as keyof FormValues, { message: e?.message })
    },
  })

  const serverError = apiError(save.error)
  const fieldShown = serverError?.details?.field && (FIELD_MAP[serverError.details.field] ?? serverError.details.field) in toForm(null)

  const blocker = useBlocker(() => !allowLeave.current && (isDirty || save.isPending))
  useBeforeUnload(useCallback(event => {
    if (!allowLeave.current && (isDirty || save.isPending)) {
      event.preventDefault()
      event.returnValue = ''
    }
  }, [isDirty, save.isPending]))

  return <Stack spacing={3} className="live-member-editor">
    <Button className="live-member-back" startIcon={<ArrowLeft size={16} />} onClick={onClose} disabled={save.isPending}>返回家庭成員</Button>
    <Box className="live-family-title">
      <Box className="live-family-title-icon"><UserPlus size={23} aria-hidden="true" /></Box>
      <Typography component="h1" variant="h4">{member ? '編輯成員' : '新增成員'}</Typography>
    </Box>
    <form noValidate onSubmit={handleSubmit(values => save.mutate(values))}>
      <fieldset className="live-member-fields" disabled={save.isPending}>
        {save.isError && !fieldShown && <Alert severity="error" sx={{ mb: 3 }}>{serverError?.message ?? '儲存失敗，請稍後再試。'}</Alert>}
        <Box className="live-member-editor-columns">
          <Card component="section"><CardContent>
            <Typography component="h2" variant="h6" className="live-member-section-title"><UserRound size={19} aria-hidden="true" />基本資料</Typography>
            <Box className="live-member-basic-grid">
              <Box className="live-member-field-wide"><FormTextField name="display_name" control={control} label="名稱" helperText="可用暱稱，不需要真實姓名" fullWidth /></Box>
              <Controller name="birth_date" control={control} render={({ field: { ref, ...field } }) => (
                <TextField label="出生日期" type="date" {...field} inputRef={ref}
                  slotProps={{ inputLabel: { shrink: true }, htmlInput: { max: today() } }}
                  error={!!errors.birth_date}
                  helperText={errors.birth_date?.message ?? (age !== null ? `目前 ${age} 歲` : undefined)} />
              )} />
              <Controller name="equation_sex" control={control} render={({ field }) => (
                <TextField select label="計算用性別" {...field} error={!!errors.equation_sex}
                  helperText={errors.equation_sex?.message ?? '國健署熱量公式使用的性別分支'}>
                  <MenuItem value="F">生理女</MenuItem><MenuItem value="M">生理男</MenuItem>
                </TextField>
              )} />
              <FormTextField name="weight_kg" control={control} label="體重（kg）" inputMode="decimal" />
              <Box>
                <Controller name="activity_category" control={control} render={({ field }) => (
                  <TextField select label="活動程度" {...field} fullWidth error={!!errors.activity_category}
                    helperText={errors.activity_category?.message}>
                    {options.activity_categories.map(a => <MenuItem key={a} value={a}>{ACTIVITY_LABELS[a]}</MenuItem>)}
                  </TextField>
                )} />
                <Tooltip title="低：多為靜態生活；稍低：少量走動或輕度活動；適度：較多走動或中等活動；高：體力工作或較多高強度運動。">
                  <IconButton size="small" aria-label="活動程度說明"><CircleHelp size={17} /></IconButton>
                </Tooltip>
              </Box>
            </Box>
          </CardContent></Card>
          <Card component="section"><CardContent>
            <Typography component="h2" variant="h6" className="live-member-section-title"><Utensils size={19} aria-hidden="true" />飲食設定</Typography>
            <Stack spacing={3}>
              <Controller name="portion" control={control} render={({ field }) => (
                <FormControl error={!!errors.portion}>
                  <FormLabel id="member-portion-label">食量</FormLabel>
                  <RadioGroup row aria-labelledby="member-portion-label" {...field} className="live-member-portion-options">
                    {options.portions.map(p => <FormControlLabel key={p} value={p} control={<Radio size="small" />} label={`${p}n`} title={PORTION_HINT[p] ?? p} />)}
                  </RadioGroup>
                  <FormHelperText>{errors.portion?.message ?? '1n 為標準食譜的一位成人餐份；倍數不代表固定熱量。'}</FormHelperText>
                </FormControl>
              )} />
              <Controller name="goal" control={control} render={({ field }) => (
                <TextField select label="飲食目標" {...field} error={!!errors.goal}
                  helperText={errors.goal?.message ?? (isMinor ? '未滿 19 歲只提供一般餐食' : undefined)}>
                  {options.goals.map(g => <MenuItem key={g} value={g} disabled={isMinor && !options.minor_goals.includes(g)}>{GOAL_LABELS[g]}</MenuItem>)}
                </TextField>
              )} />
            </Stack>
          </CardContent></Card>
        </Box>
        <Card component="section" className="live-member-restriction-panel"><CardContent>
          <Typography component="h2" variant="h6" className="live-member-section-title"><ShieldCheck size={19} aria-hidden="true" />飲食限制</Typography>
          <Box className="live-member-editor-columns">
            <Controller name="allergens" control={control} render={({ field }) => (
              <IngredientPicker label="過敏食材" value={field.value} onChange={field.onChange}
                error={!!errors.allergens} helperText={errors.allergens?.message} />
            )} />
            <Controller name="avoided" control={control} render={({ field }) => (
              <IngredientPicker label="禁吃食材" value={field.value} onChange={field.onChange}
                error={!!errors.avoided} helperText={errors.avoided?.message} />
            )} />
          </Box>
        </CardContent></Card>
      </fieldset>
      <Box className="live-family-actions" sx={{ mt: 3 }}>
        <Button variant="outlined" onClick={onClose} disabled={save.isPending}>取消</Button>
        <Button type="submit" variant="contained" startIcon={<Check size={18} />} disabled={save.isPending}>{save.isPending ? '儲存中…' : '儲存'}</Button>
      </Box>
    </form>
    <Dialog open={blocker.state === 'blocked'} onClose={() => blocker.state === 'blocked' && blocker.reset()}>
      <DialogTitle>{save.isPending ? '正在儲存成員' : '離開前要捨棄修改嗎？'}</DialogTitle>
      <DialogContent><Typography>{save.isPending ? '請稍候，儲存完成後會自動返回家庭成員。' : '這次修改尚未儲存，離開後將不會保留。'}</Typography></DialogContent>
      <DialogActions>
        <Button onClick={() => blocker.state === 'blocked' && blocker.reset()}>繼續編輯</Button>
        <Button color="error" disabled={save.isPending} onClick={() => {
          if (blocker.state === 'blocked') { allowLeave.current = true; blocker.proceed() }
        }}>捨棄並離開</Button>
      </DialogActions>
    </Dialog>
  </Stack>
}
