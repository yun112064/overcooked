import { zodResolver } from '@hookform/resolvers/zod'
import {
  Alert, Button, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField,
} from '@mui/material'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { z } from 'zod'
import { api, apiError } from '../api/client'
import {
  ACTIVITY_LABELS, GOAL_LABELS, type Activity, type Goal, type Member, type MemberInput, type MemberOptions,
} from '../api/types'
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
  open: boolean
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

export default function MemberFormDialog({ open, member, options, onClose }: Props) {
  const queryClient = useQueryClient()
  const { control, register, handleSubmit, reset, setError, setValue, getValues, formState: { errors } } =
    useForm<FormValues>({ mode: 'onTouched', resolver: zodResolver(buildSchema(options)), defaultValues: toForm(member) })

  useEffect(() => { if (open) reset(toForm(member)) }, [open, member, reset])

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

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{member ? '編輯成員' : '新增成員'}</DialogTitle>
      <form noValidate onSubmit={handleSubmit((v) => save.mutate(v))}>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {save.isError && !fieldShown && <Alert severity="error">{serverError?.message ?? '儲存失敗，請稍後再試。'}</Alert>}
            <TextField label="成員名稱" {...register('display_name')}
              error={!!errors.display_name} helperText={errors.display_name?.message ?? '可用暱稱，不需要真實姓名'} />
            <TextField label="出生日期" type="date" {...register('birth_date')}
              slotProps={{ inputLabel: { shrink: true }, htmlInput: { max: today() } }}
              error={!!errors.birth_date}
              helperText={errors.birth_date?.message ?? (age !== null ? `目前 ${age} 歲` : undefined)} />
            <Controller name="equation_sex" control={control} render={({ field }) => (
              <TextField select label="公式性別" {...field} error={!!errors.equation_sex}
                helperText={errors.equation_sex?.message ?? '國健署熱量公式使用的性別分支'}>
                <MenuItem value="F">女</MenuItem>
                <MenuItem value="M">男</MenuItem>
              </TextField>
            )} />
            <TextField label="體重（公斤）" inputMode="decimal" {...register('weight_kg')}
              error={!!errors.weight_kg} helperText={errors.weight_kg?.message} />
            <Controller name="activity_category" control={control} render={({ field }) => (
              <TextField select label="活動量" {...field} error={!!errors.activity_category}
                helperText={errors.activity_category?.message}>
                {options.activity_categories.map((a) => <MenuItem key={a} value={a}>{ACTIVITY_LABELS[a]}</MenuItem>)}
              </TextField>
            )} />
            <Controller name="goal" control={control} render={({ field }) => (
              <TextField select label="飲食目標" {...field} error={!!errors.goal}
                helperText={errors.goal?.message ?? (isMinor ? '未滿 19 歲只提供一般餐食' : undefined)}>
                {options.goals.map((g) => (
                  <MenuItem key={g} value={g} disabled={isMinor && !options.minor_goals.includes(g)}>
                    {GOAL_LABELS[g]}
                  </MenuItem>
                ))}
              </TextField>
            )} />
            <Controller name="portion" control={control} render={({ field }) => (
              <TextField select label="相對食量 n" {...field} error={!!errors.portion}
                helperText={errors.portion?.message ?? '1n 為標準食譜的一位成人餐份'}>
                {options.portions.map((p) => <MenuItem key={p} value={p}>{PORTION_HINT[p] ?? p}</MenuItem>)}
              </TextField>
            )} />
            <Controller name="allergens" control={control} render={({ field }) => (
              <IngredientPicker label="過敏食材" value={field.value} onChange={field.onChange}
                error={!!errors.allergens} helperText={errors.allergens?.message} />
            )} />
            <Controller name="avoided" control={control} render={({ field }) => (
              <IngredientPicker label="禁吃食材" value={field.value} onChange={field.onChange}
                error={!!errors.avoided} helperText={errors.avoided?.message} />
            )} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" variant="contained" disabled={save.isPending}>儲存</Button>
        </DialogActions>
      </form>
    </Dialog>
  )
}
