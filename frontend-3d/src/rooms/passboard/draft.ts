/**
 * 問卷表單的資料規則（純函式，不碰網路與畫面）：欄位格式、表單 ↔ 草稿的轉換、五個步驟各管哪些欄位、
 * 後端回報的問題（issues）該標在哪一步、鍋裡要放到第幾樣料。
 * 規則和舊前端的問卷編輯頁相同，只是步驟的分法照 DESIGN.md 5.4。
 */
import type { FieldPath } from 'react-hook-form'
import { z } from 'zod'
import type { Issue, MealRequest, MealRequestDraft, MealType, Member, StapleOptions } from '../../api/types'
import { POT_STEPS, draftSteps } from '../../pot/potState'

export const STEPS = [
  { short: '日期與預算', title: '哪一餐？預算多少？' },
  { short: '成員', title: '這一餐誰要吃？' },
  { short: '現有食材', title: '家裡現在有什麼？' },
  { short: '主食與口味', title: '想吃什麼？' },
  { short: '確認', title: '確認，開始煮' },
] as const

export const LAST_STEP = STEPS.length - 1
export const MEALS: MealType[] = ['breakfast', 'lunch', 'dinner']

const participantSchema = z.object({
  dining: z.boolean(),
  bento: z.boolean(),
  bento_meal: z.enum(['breakfast', 'lunch', 'dinner']),
})

export const schema = z.object({
  meal_date: z.string(),
  meal_type: z.union([z.enum(['breakfast', 'lunch', 'dinner']), z.literal('')]),
  budget: z.string().trim().refine(
    (v) => v === '' || (/^\d{1,6}(\.\d{1,2})?$/.test(v) && Number(v) > 0 && Number(v) <= 100000),
    '預算需為大於 0 的金額（最多兩位小數）',
  ),
  staple: z.string().max(30, '最多 30 個字'),
  dish_count: z.string(),
  taste_preferences: z.string().max(200, '最多 200 個字'),
  specified_dishes: z.array(z.string().trim().min(1).max(50, '每道菜名最多 50 個字')
    // 與後端相同：只有數字或符號不是菜名
    .refine((v) => /\p{L}/u.test(v), { error: (iss) => `「${String(iss.input)}」不是菜名，請輸入菜名（不能只有數字或符號）` }))
    .max(10, '最多指定 10 道'),
  participants: z.record(z.string(), participantSchema),
  available: z.array(z.object({ ingredient_id: z.string(), name: z.string().nullable() })),
})
export type FormValues = z.infer<typeof schema>

/** 每一步要檢查格式的表單欄位（換步驟前只檢查這一步的）。 */
export const STEP_FIELDS: FieldPath<FormValues>[][] = [
  ['meal_date', 'meal_type', 'budget'],
  ['participants'],
  ['available'],
  ['staple', 'dish_count', 'taste_preferences', 'specified_dishes'],
  [],
]

/** 後端的欄位名稱（issues 的 field、錯誤的 details.field）屬於哪一步；不認得的放在確認步驟。 */
const SERVER_FIELD_STEP: Record<string, number> = {
  meal_date: 0, meal_type: 0, budget: 0,
  participants: 1,
  available_ingredient_ids: 2,
  staple: 3, dish_count: 3, taste_preferences: 3, specified_dishes: 3,
}

/** 「draft.participants.0.member_id」→「participants」。 */
export function serverFieldName(field: string): string {
  return field.replace(/^draft\./, '').split('.')[0]
}

export function stepOfServerField(field: string): number {
  return SERVER_FIELD_STEP[serverFieldName(field)] ?? LAST_STEP
}

/** 後端欄位名稱 → 表單欄位名稱（只有已有食材的名字不同）；表單沒有這個欄位回傳 null。 */
export function formFieldOf(field: string): keyof FormValues | null {
  const name = serverFieldName(field)
  const key = name === 'available_ingredient_ids' ? 'available' : name
  return key in schema.shape ? key as keyof FormValues : null
}

/**
 * 某一步現在要提醒的問題（後端回報的 issues）。furthest＝這次進來走到過的最後一步。
 * - 「還沒填」（REQUIRED）：等使用者走過那一步才提醒，不在還沒輪到的步驟上先亮紅點。
 * - 其餘（預算太低、主食不在清單、成員已刪除、指定菜太多…）是已經存下來的內容有問題，一打開就要看得到。
 */
export function issuesForStep(issues: readonly Issue[], step: number, furthest: number): Issue[] {
  return issues.filter((issue) =>
    stepOfServerField(issue.field) === step && (issue.reason !== 'REQUIRED' || furthest > step))
}

/** 步驟條上哪幾步要標紅點。確認步驟本身不標：它只是把前面的問題列出來。 */
export function stepsWithIssues(issues: readonly Issue[], furthest: number): boolean[] {
  return STEPS.map((_, step) => step !== LAST_STEP && issuesForStep(issues, step, furthest).length > 0)
}

/** 已存的主食文字（可能是別名或「無」等寫法）對應到清單上的標準選項；對不到回傳 null。 */
export function stapleChoice(value: string, staples: StapleOptions): string | null {
  const v = value.trim()
  if (!v) return ''
  if (v === staples.none_value || staples.none_values.includes(v)) return staples.none_value
  const hit = staples.options.find((o) => o.label === v || o.aliases.includes(v))
  return hit ? hit.label : null
}

export function toForm(row: MealRequest, members: readonly Member[]): FormValues {
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

export function toDraft(v: FormValues): MealRequestDraft {
  return {
    mode: 'questionnaire',
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

/** 已經有結果、已採用、生成中或失敗的問卷直接打開在確認步驟（狀態與重試都在那裡）。 */
const OPEN_AT_CONFIRM = new Set(['result_ready', 'awaiting_decision', 'confirmed', 'queued', 'running', 'failed'])

export function initialStep(status: string): number {
  return OPEN_AT_CONFIRM.has(status) ? LAST_STEP : 0
}

/**
 * 網址上的 ?step=N（1 起算，例如冰箱把手的「先拍食材」帶 step=3）→ 一開始停在第幾步（0 起算）。
 * 只有草稿才照網址走；其他狀態一律開在確認步驟（那裡有狀態與重試）。不是 1–5 的整數就當作沒給。
 */
export function requestedStep(raw: string | null, status: string): number | null {
  if (status !== 'draft' || raw === null || !/^[1-9]$/.test(raw)) return null
  const index = Number(raw) - 1
  return index <= LAST_STEP ? index : null
}

/**
 * 鍋裡該有幾樣料（0–5），交給 potState 的 stepsDone。
 * 依後端存下來的內容算（和廚房、客廳看到的同一口鍋一致）：五步都好（complete）才全滿。
 * 第 3 步沒有必填欄位，廚房只能靠「選了食材」判斷；在單板上，使用者走過第 3 步（furthestStep ≥ 3）就算完成。
 */
export function potSteps(row: Pick<MealRequest, 'id' | 'status' | 'version' | 'complete' | 'issues' | 'draft'>, furthestStep: number): number {
  if (row.complete) return POT_STEPS
  const saved = draftSteps(row)
  return saved === 2 && furthestStep >= 3 ? 3 : saved
}

/** 指定菜色的輸入：依逗號、頓號、換行切開，去掉空白與重複。 */
export function splitDishNames(text: string): string[] {
  return [...new Set(text.split(/[,，、\n]/).map((s) => s.trim()).filter(Boolean))]
}

/** 顯示用取整；不是數字就原樣顯示（後端給的是精確字串）。 */
export function fmt(value: string, digits = 1): string {
  const n = Number(value)
  return Number.isFinite(n) ? n.toFixed(digits) : value
}
