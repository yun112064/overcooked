/**
 * 新增／編輯成員的表單（舊前端的 MemberFormDialog 換皮）。放在左邊的面板裡；手機是下方抽屜。
 * 規則都在 memberRules.ts；這裡只負責畫欄位、送出，和把後端的錯誤放到對的地方。
 */
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, type ReactNode } from 'react'
import { Controller, useForm, useWatch } from 'react-hook-form'
import { api, apiError } from '../../api/client'
import { ACTIVITY_LABELS, GOAL_LABELS, type Member, type MemberOptions } from '../../api/types'
import { Button } from '../../components/Button'
import { Notice } from '../../components/Notice'
import { Panel } from '../../components/Panel'
import { membersQuery } from '../../session/queries'
import { IngredientPicker } from './IngredientPicker'
import {
  PORTION_HINT, ageOn, backendToday, buildSchema, isMinor, minorGoalHint, serverField, toBody, toForm,
  type FormValues,
} from './memberRules'
import panelStyles from './panels.module.css'
import styles from './MemberForm.module.css'

type Props = {
  /** null＝新增 */
  member: Member | null
  options: MemberOptions
  onClose: () => void
  onSaved: (member: Member) => void
}

type FieldProps = {
  label: string
  error?: string
  hint?: string
  children: (control: { id: string; 'aria-invalid': boolean; 'aria-describedby'?: string }) => ReactNode
}

/** 一個欄位：標籤、輸入元件、下面一行說明或錯誤（錯誤優先）。 */
function Field({ label, error, hint, children }: FieldProps) {
  const id = useId()
  const hintId = useId()
  const text = error ?? hint
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      {children({ id, 'aria-invalid': !!error, 'aria-describedby': text ? hintId : undefined })}
      {text && <p id={hintId} className={error ? styles.error : styles.hint}>{text}</p>}
    </div>
  )
}

export function MemberForm({ member, options, onClose, onSaved }: Props) {
  const queryClient = useQueryClient()
  const formId = useId()
  const { control, register, handleSubmit, reset, setError, setValue, getValues, formState: { errors } } =
    useForm<FormValues>({ mode: 'onTouched', resolver: zodResolver(buildSchema(options)), defaultValues: toForm(member) })

  // 成員資料換了（例如版本衝突後重新讀到別人改的內容）就整張重填，不能拿舊畫面的值蓋掉新版本
  useEffect(() => { reset(toForm(member)) }, [member, reset])

  const today = backendToday()
  const birthDate = useWatch({ control, name: 'birth_date' }) ?? ''
  const age = ageOn(birthDate, today)
  const minor = isMinor(birthDate, today, options)
  // 改成未成年的生日時，原本選的成人目標要跟著換掉，不然送出一定被退回
  useEffect(() => {
    if (minor && !options.minor_goals.includes(getValues('goal'))) setValue('goal', options.minor_goals[0] ?? 'general')
  }, [minor, options.minor_goals, getValues, setValue])

  const save = useMutation({
    mutationFn: async (values: FormValues) => {
      const body = toBody(values)
      return member
        ? (await api.put<Member>(`/members/${member.id}`, { ...body, version: member.version })).data
        : (await api.post<Member>('/members', body)).data
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: membersQuery.queryKey })
      onSaved(saved)
    },
    onError: (err) => {
      const error = apiError(err)
      // 後端是最終驗證者：它指出哪一欄，就把訊息放在那一欄
      const field = serverField(error)
      if (field) setError(field, { message: error?.message })
      // 版本對不上＝別人剛改過，或這位成員已經不在：重新讀清單，表單才拿得到新的版本
      if (error?.code === 'VERSION_CONFLICT' || error?.code === 'NOT_FOUND') {
        void queryClient.invalidateQueries({ queryKey: membersQuery.queryKey })
      }
    },
  })

  const serverError = apiError(save.error)
  const shownOnField = serverField(serverError) !== null
  const title = member ? '編輯成員' : '新增成員'

  return (
    <Panel
      title={title} side="left" onClose={onClose} className={panelStyles.wallLeft}
      footer={(
        <>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" form={formId} variant="primary" busy={save.isPending}>
            {save.isPending ? '儲存中…' : '儲存'}
          </Button>
        </>
      )}
    >
      <form id={formId} className={styles.form} noValidate onSubmit={handleSubmit((values) => save.mutate(values))}>
        {save.isError && !shownOnField && (
          <Notice tone="error">
            {serverError?.message ?? '儲存失敗，請稍後再試。'}
            {serverError?.code === 'VERSION_CONFLICT' && '最新的資料會自動帶進這張表單，請確認後再儲存一次。'}
          </Notice>
        )}

        <Field label="成員名稱" error={errors.display_name?.message} hint="可用暱稱，不需要真實姓名">
          {(a) => <input type="text" autoComplete="off" {...a} {...register('display_name')} />}
        </Field>

        <Field label="出生日期" error={errors.birth_date?.message} hint={age !== null ? `目前 ${age} 歲` : undefined}>
          {(a) => <input type="date" max={today} {...a} {...register('birth_date')} />}
        </Field>

        <Field label="公式性別" error={errors.equation_sex?.message} hint="國健署熱量公式使用的性別分支">
          {(a) => (
            <select {...a} {...register('equation_sex')}>
              <option value="F">女</option>
              <option value="M">男</option>
            </select>
          )}
        </Field>

        <Field label="體重（公斤）" error={errors.weight_kg?.message}>
          {(a) => <input type="text" inputMode="decimal" autoComplete="off" {...a} {...register('weight_kg')} />}
        </Field>

        <Field label="活動量" error={errors.activity_category?.message}>
          {(a) => (
            <select {...a} {...register('activity_category')}>
              {options.activity_categories.map((value) => (
                <option key={value} value={value}>{ACTIVITY_LABELS[value] ?? value}</option>
              ))}
            </select>
          )}
        </Field>

        <Field label="飲食目標" error={errors.goal?.message} hint={minor ? minorGoalHint(options) : undefined}>
          {(a) => (
            <select {...a} {...register('goal')}>
              {options.goals.map((value) => (
                <option key={value} value={value} disabled={minor && !options.minor_goals.includes(value)}>
                  {GOAL_LABELS[value] ?? value}
                </option>
              ))}
            </select>
          )}
        </Field>

        <Field label="相對食量 n" error={errors.portion?.message} hint="1n 為標準食譜的一位成人餐份">
          {(a) => (
            <select {...a} {...register('portion')}>
              {options.portions.map((value) => <option key={value} value={value}>{PORTION_HINT[value] ?? value}</option>)}
            </select>
          )}
        </Field>

        <Controller name="allergens" control={control} render={({ field }) => (
          <IngredientPicker
            label="過敏食材" value={field.value} onChange={field.onChange} onBlur={field.onBlur}
            error={errors.allergens?.message} helperText="有登記的食材，排菜時一定會避開"
          />
        )} />
        <Controller name="avoided" control={control} render={({ field }) => (
          <IngredientPicker
            label="禁吃食材" value={field.value} onChange={field.onChange} onBlur={field.onBlur}
            error={errors.avoided?.message}
          />
        )} />
      </form>
    </Panel>
  )
}
