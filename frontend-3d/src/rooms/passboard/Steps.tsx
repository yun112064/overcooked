/**
 * 問卷前四步的欄位（DESIGN.md 5.4）。欄位、說明文字與規則照舊前端的問卷編輯頁，只是分到不同的步驟：
 *   1 日期、餐別、預算；2 成員與便當；3 家裡現有的食材（辨識、搜尋，可略過）；
 *   4 主食、菜數、口味、指定菜色，以及成員的過敏與不吃的食材（只能看）
 */
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Controller, useWatch, type UseFormReturn } from 'react-hook-form'
import { Link } from 'react-router'
import { api, errorMessage } from '../../api/client'
import { MEAL_LABELS, type IngredientRef, type Issue, type MealRequest, type Member, type StapleOptions } from '../../api/types'
import { Notice } from '../../components/Notice'
import { Spinner } from '../../components/Spinner'
import { Restrictions, TargetsPreview } from './Confirm'
import { Field } from './fields'
import { MEALS, splitDishNames, stapleChoice, type FormValues } from './draft'
import { IngredientPicker, RecognitionCapture } from './Pantry'
import { useTeamStatus } from './teamStatus'
import styles from './Paper.module.css'

type StepProps = {
  form: UseFormReturn<FormValues>
  /** 後端目前存的問卷（issues、預算下限、限制都以它為準） */
  row: MealRequest
  /** 生成中或已採用：只能看 */
  readOnly: boolean
}

/** 這一步後端回報還沒填、或存下來的內容要調整的地方。role 不用 alert：這是提醒，不是剛發生的錯誤。 */
export function StepIssues({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return null
  return (
    <Notice tone="warn" title="這一步還有要補或要改的">
      <ul className={styles.issueList}>{issues.map((issue) => <li key={issue.field + issue.reason}>{issue.message}</li>)}</ul>
    </Notice>
  )
}

/** 第 1 步：日期、餐別、預算。 */
export function StepWhen({ form, row, readOnly }: StepProps) {
  const { register, control, formState: { errors } } = form
  // 預算低於估算下限（後端判斷）：只在欄位值還是已儲存的那個預算時標紅，改了就等下次儲存重新判斷
  const budgetValue = useWatch({ control, name: 'budget' })
  const budgetLow = row.issues.find((i) => i.field === 'budget' && i.reason === 'BUDGET_TOO_LOW')
  const lowNow = !!budgetLow && Number(budgetValue) === Number(row.draft.budget)
  return (
    <fieldset className={styles.fields} disabled={readOnly}>
      <legend className="visually-hidden">日期、餐別與預算</legend>
      <Field label="餐食日期" error={errors.meal_date?.message}>
        {(a11y) => <input type="date" {...a11y} {...register('meal_date')} />}
      </Field>
      <Field label="餐別" hint="早餐 30%、午餐 30%、晚餐 40% 的每日目標" error={errors.meal_type?.message}>
        {(a11y) => (
          <select {...a11y} {...register('meal_type')}>
            <option value="">未選擇</option>
            {MEALS.map((m) => <option key={m} value={m}>{MEAL_LABELS[m]}</option>)}
          </select>
        )}
      </Field>
      <Field
        label="預算（新台幣）"
        error={errors.budget?.message ?? (lowNow ? budgetLow?.message : undefined)}
        hint={row.budget_floor
          ? `這一餐估算最低約 ${row.budget_floor} 元（依菜數與用餐人數）；低於此金額不能送出生成`
          : '以需補買食材的比例成本判斷是否在預算內；選好菜數與成員後會顯示最低金額'}
      >
        {(a11y) => <input inputMode="decimal" autoComplete="off" {...a11y} {...register('budget')} />}
      </Field>
    </fieldset>
  )
}

/** 第 2 步：這餐誰一起吃、誰另外帶便當。 */
export function StepMembers({ form, row, readOnly, members }: StepProps & { members: Member[] }) {
  const { control, formState: { errors } } = form
  const groupError = errors.participants?.message ?? errors.participants?.root?.message
  return (
    <div className={styles.stack}>
      {members.length === 0 && (
        <Notice>還沒有家庭成員。<Link to="/family">先到全家福新增成員</Link></Notice>
      )}
      <p className={styles.fieldHint}>勾選這餐要一起吃的成員；帶便當代表另外多準備一份，同一人可同時勾選。</p>
      {typeof groupError === 'string' && <p className={styles.fieldError} role="alert">{groupError}</p>}
      <ul className={styles.members}>
        {members.map((m) => (
          <Controller
            key={m.id} name={`participants.${m.id}`} control={control}
            render={({ field }) => {
              const value = field.value ?? { dining: false, bento: false, bento_meal: 'lunch' as const }
              return (
                <li className={value.dining || value.bento ? styles.memberOn : undefined}>
                  <fieldset disabled={readOnly}>
                    <legend>{m.display_name}<small>食量 {m.portion}n</small></legend>
                    <label className={styles.check}>
                      <input
                        type="checkbox" checked={value.dining} onBlur={field.onBlur}
                        onChange={(event) => field.onChange({ ...value, dining: event.target.checked })}
                      />
                      一起用餐
                    </label>
                    <label className={styles.check}>
                      <input
                        type="checkbox" checked={value.bento} onBlur={field.onBlur}
                        onChange={(event) => field.onChange({ ...value, bento: event.target.checked })}
                      />
                      帶便當
                    </label>
                    {value.bento && (
                      <label className={styles.inlineSelect}>
                        便當餐別
                        <select
                          value={value.bento_meal}
                          onChange={(event) => field.onChange({ ...value, bento_meal: event.target.value as FormValues['participants'][string]['bento_meal'] })}
                        >
                          {MEALS.map((meal) => <option key={meal} value={meal}>{MEAL_LABELS[meal]}</option>)}
                        </select>
                      </label>
                    )}
                  </fieldset>
                </li>
              )
            }}
          />
        ))}
      </ul>
      <TargetsPreview row={row} />
      {row.targets_preview.status !== 'incomplete' && <p className={styles.fieldHint}>目標依上次儲存的成員與餐別計算；個人設定請到全家福修改。</p>}
    </div>
  )
}

/** 第 3 步：家裡現有的食材。可以拍照辨識、直接搜尋，或整步略過。 */
export function StepPantry({ form, row, readOnly }: StepProps) {
  const { control, getValues, setValue, formState: { errors } } = form
  const team = useTeamStatus()
  const recognition = team.data?.recognition
  const add = (ingredient: IngredientRef) => {
    const current = getValues('available')
    if (!current.some((i) => i.ingredient_id === ingredient.ingredient_id)) {
      setValue('available', [...current, ingredient], { shouldDirty: true })
    }
  }
  return (
    <div className={styles.stack}>
      <p className={styles.fieldHint}>
        選擇家中已有的食材種類。已有的種類視為足夠這餐使用，不需要輸入重量；沒選的食材會按整餐需求量補買。這一步可以略過。
      </p>
      {recognition && recognition.status !== 'online' && (
        <Notice tone="warn">蔬菜辨識目前{recognition.status === 'offline' ? '離線' : '未設定'}，照片可能辨識不了；可以直接在下面搜尋加入。</Notice>
      )}
      <RecognitionCapture mealRequestId={row.id} disabled={readOnly} onAdd={add} />
      <Controller
        name="available" control={control}
        render={({ field }) => (
          <IngredientPicker
            label="搜尋食材加入" value={field.value} onChange={field.onChange}
            disabled={readOnly} error={errors.available?.message}
          />
        )}
      />
    </div>
  )
}

/** 指定菜色：打菜名按 Enter（或「加入」）變成一張小標籤；可以一次貼好幾道（用頓號或逗號分開）。 */
function DishInput({ value, onChange, onBlur, disabled, error, hint }: {
  value: string[]; onChange: (value: string[]) => void; onBlur: () => void; disabled: boolean
  error: string | undefined; hint: string
}) {
  const [text, setText] = useState('')
  const commit = () => {
    const names = splitDishNames(text).filter((name) => !value.includes(name))
    if (names.length > 0) onChange([...value, ...names])
    setText('')
  }
  return (
    <div className={styles.stack}>
      {value.length > 0 && (
        <ul className={styles.chips} aria-label="已指定的菜">
          {value.map((name) => (
            <li key={name} className={styles.pickedChip}>
              {name}
              <button type="button" aria-label={`移除${name}`} disabled={disabled} onClick={() => onChange(value.filter((v) => v !== name))}>✕</button>
            </li>
          ))}
        </ul>
      )}
      <Field label="指定菜色" error={error} hint={hint}>
        {(a11y) => (
          <span className={styles.inputWithButton}>
            <input
              {...a11y} value={text} disabled={disabled} autoComplete="off" enterKeyHint="done"
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                // 中文輸入法選字時的 Enter 不算
                if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
                event.preventDefault()
                commit()
              }}
              // 打了字沒按 Enter 就離開：照樣加入，免得換步驟時悄悄不見
              onBlur={() => { commit(); onBlur() }}
            />
            <button type="button" className={styles.smallButton} disabled={disabled || text.trim() === ''} onClick={commit}>加入</button>
          </span>
        )}
      </Field>
    </div>
  )
}

/** 第 4 步：主食、菜數、口味、指定菜色；最下面是成員的過敏與不吃的食材（由成員資料決定）。 */
export function StepTaste({ form, row, readOnly }: StepProps) {
  const { register, control, formState: { errors } } = form
  const staples = useQuery({
    queryKey: ['staple-options'],
    queryFn: async () => (await api.get<StapleOptions>('/meal-requests/staple-options')).data,
    staleTime: Infinity,
  })
  const dishCount = useWatch({ control, name: 'dish_count' })
  const specified = useWatch({ control, name: 'specified_dishes' })
  // 單一菜名的錯誤掛在陣列各項上；整體錯誤（數量、後端回報）掛在欄位本身
  const dishError = errors.specified_dishes?.message
    ?? errors.specified_dishes?.root?.message
    ?? (Array.isArray(errors.specified_dishes) ? errors.specified_dishes.find((e) => e?.message)?.message : undefined)

  return (
    <div className={styles.stack}>
      <fieldset className={styles.fields} disabled={readOnly}>
        <legend className="visually-hidden">主食、菜數、口味與指定菜色</legend>
        <Controller
          name="staple" control={control}
          render={({ field }) => {
            // 決策 #52：只能從後端清單選（不可自由輸入）；舊草稿的清單外文字顯示為待改選
            if (!staples.data) {
              return staples.isError
                ? <Notice tone="error">無法載入主食清單：{errorMessage(staples.error)}</Notice>
                : <Spinner label="正在載入主食清單…" />
            }
            const choice = stapleChoice(field.value, staples.data)
            const stale = choice === null ? field.value : null
            const staleIssue = row.issues.find((i) => i.field === 'staple' && i.reason === 'STAPLE_NOT_SUPPORTED')
            return (
              <Field
                label="主食"
                error={errors.staple?.message ?? (stale !== null ? staleIssue?.message ?? `「${stale}」不在清單中，請改選` : undefined)}
                hint={`從清單選擇；不吃主食請選「${staples.data.none_value}」`}
              >
                {(a11y) => (
                  <select
                    {...a11y} name={field.name} ref={field.ref} onBlur={field.onBlur}
                    value={stale ?? choice ?? ''} onChange={(event) => field.onChange(event.target.value)}
                  >
                    <option value="">未選擇</option>
                    {stale !== null && <option value={stale} disabled>{stale}（不在清單，請改選）</option>}
                    {staples.data.options.map((o) => <option key={o.label} value={o.label}>{o.label}</option>)}
                    <optgroup label="不需要主食">
                      <option value={staples.data.none_value}>{staples.data.none_value}</option>
                    </optgroup>
                  </select>
                )}
              </Field>
            )
          }}
        />
        <Field label="菜數" hint="指定菜色也會計入菜數" error={errors.dish_count?.message}>
          {(a11y) => (
            <select {...a11y} {...register('dish_count')}>
              <option value="">未選擇</option>
              {Array.from({ length: 10 }, (_, i) => String(i + 1)).map((n) => <option key={n} value={n}>{n} 道</option>)}
            </select>
          )}
        </Field>
        <Field label="口味偏好" hint="例如：清淡、少辣（會盡量滿足）" error={errors.taste_preferences?.message}>
          {(a11y) => <textarea rows={2} {...a11y} {...register('taste_preferences')} />}
        </Field>
        <Controller
          name="specified_dishes" control={control}
          render={({ field }) => (
            <DishInput
              value={field.value} onChange={field.onChange} onBlur={field.onBlur} disabled={readOnly} error={dishError}
              hint={`輸入菜名後按 Enter；指定菜一定會出現在菜單中${dishCount ? `（已指定 ${specified?.length ?? 0}／${dishCount} 道）` : ''}`}
            />
          )}
        />
      </fieldset>
      <section className={styles.stack} aria-label="不吃的食材">
        <h4>不吃的食材</h4>
        <Restrictions row={row} />
        <p className={styles.fieldHint}>
          過敏與不吃的食材來自這餐成員的個人資料（依上次儲存的成員），配菜時一定會排除；
          要修改請到<Link to="/family">全家福</Link>。
        </p>
      </section>
    </div>
  )
}
