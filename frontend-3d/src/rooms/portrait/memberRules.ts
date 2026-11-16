/**
 * 成員表單的規則（從舊前端的 MemberFormDialog 搬過來，規則一條都不能少）：
 * 驗證、表單值和 API 內容的互轉、後端欄位錯誤要顯示在哪一欄。後端仍是最後的驗證者。
 */
import { z } from 'zod'
import type { ApiErrorBody } from '../../api/client'
import type { Activity, Goal, Member, MemberInput, MemberOptions } from '../../api/types'

export const PORTION_HINT: Record<string, string> = {
  '0.7': '0.7n（食量較小）',
  '0.85': '0.85n',
  '1': '1n（標準成人一份）',
  '1.15': '1.15n',
  '1.3': '1.3n（食量較大）',
}

/**
 * 後端算「今天」用的是 UTC 日期（出生日期不能晚於今天、年齡都照它算），
 * 所以這裡也用 UTC，不用本機日期——不然台灣早上 8 點前選「今天」會被後端退回。
 */
export function backendToday(now: Date = new Date()): string {
  return now.toISOString().slice(0, 10)
}

/** 到 on 那天的足歲；birth 不是 YYYY-MM-DD 就回 null。 */
export function ageOn(birth: string, on: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(birth)) return null
  const [by, bm, bd] = birth.split('-').map(Number)
  const [ty, tm, td] = on.split('-').map(Number)
  return ty - by - (tm < bm || (tm === bm && td < bd) ? 1 : 0)
}

/** 未成年（未滿 options.adult_goals_min_age_years）只能選 options.minor_goals 裡的目標。 */
export function isMinor(birth: string, on: string, options: Pick<MemberOptions, 'adult_goals_min_age_years'>): boolean {
  const age = ageOn(birth, on)
  return age !== null && age < options.adult_goals_min_age_years
}

export function minorGoalHint(options: Pick<MemberOptions, 'adult_goals_min_age_years'>): string {
  return `未滿 ${options.adult_goals_min_age_years} 歲只提供一般餐食`
}

export function buildSchema(options: MemberOptions, today: () => string = backendToday) {
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
    activity_category: z.enum(options.activity_categories as [Activity, ...Activity[]], { error: '請選擇活動量' }),
    goal: z.enum(options.goals as [Goal, ...Goal[]], { error: '請選擇飲食目標' }),
    portion: z.enum(options.portions as [string, ...string[]], { error: '請選擇食量' }),
    allergens: z.array(ingredient),
    avoided: z.array(ingredient),
  }).superRefine((v, ctx) => {
    if (isMinor(v.birth_date, today(), options) && !options.minor_goals.includes(v.goal)) {
      ctx.addIssue({
        code: 'custom', path: ['goal'],
        message: `未滿 ${options.adult_goals_min_age_years} 歲只能選擇一般餐食`,
      })
    }
  })
}

export type FormValues = z.infer<ReturnType<typeof buildSchema>>

/** 表單一開始的值：編輯帶入成員現有資料，新增用預設值。 */
export function toForm(member: Member | null): FormValues {
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

/** 送給後端的內容；食材只送代碼。修改時呼叫端另外加上 version。 */
export function toBody(values: FormValues): MemberInput {
  return {
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
}

// 後端的欄位名稱和表單欄位名稱不同的地方
const FIELD_MAP: Record<string, keyof FormValues> = {
  allergen_ingredient_ids: 'allergens',
  avoided_ingredient_ids: 'avoided',
}

const FORM_FIELDS = new Set<string>(Object.keys(toForm(null)))

/** 後端指出是哪一欄有問題時，對應到表單的欄位；對不到（或沒指明）回 null，改顯示在表單最上面。 */
export function serverField(error: ApiErrorBody['error'] | null): keyof FormValues | null {
  const field = error?.details?.field
  if (!field) return null
  const key = FIELD_MAP[field] ?? field
  return FORM_FIELDS.has(key) ? (key as keyof FormValues) : null
}
