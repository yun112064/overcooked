import type {
  Activity, Goal, Issue, MealRequest, MealRequestDraft, MealRequestSummary, MealType, Member, MemberOptions,
  Participant, StapleOptions,
} from '../../src/api/types.ts'
import { describeFoods, expansions, food, unknownFoodIds } from './catalog.ts'
import { ApiFail, ageOn, asRecord, badFields, invalidInput, isDate, iso, localDate, num } from './core.ts'
import type { FakeState, MemberRow, RequestRow } from './core.ts'
import {
  ADULT_AGE, PORTIONS, RULE_VERSION, TargetError, computeTargets, dailyTarget, requestItems, targetsView,
} from './nutrition.ts'
import type { Targets } from './nutrition.ts'

/** 成員與問卷：輸入檢查、完整性與回應格式（規則照搬 backend 的 members.py／meal_request.py）。 */

const ACTIVITIES: Activity[] = ['sedentary', 'low_active', 'active', 'highly_active']
const GOALS: Goal[] = ['general', 'maintain', 'muscle_gain', 'fat_loss']
const MEALS: MealType[] = ['breakfast', 'lunch', 'dinner']
const MAX_RESTRICTIONS = 50
const MAX_DISHES = 10

export const today = (state: FakeState) => state.fixedToday ?? localDate(state.now())

// ---- 成員 ----

export function memberOptions(): MemberOptions {
  return {
    rule_version: RULE_VERSION, portions: PORTIONS, activity_categories: ACTIVITIES, goals: GOALS,
    adult_goals_min_age_years: ADULT_AGE, minor_goals: ['general'],
  }
}

export function memberView(state: FakeState, m: MemberRow): Member {
  return {
    id: m.id,
    display_name: m.display_name,
    birth_date: m.birth_date,
    age_years: ageOn(m.birth_date, today(state)),
    equation_sex: m.equation_sex,
    weight_kg: m.weight_kg,
    activity_category: m.activity_category,
    goal: m.goal,
    portion: m.portion,
    allergens: describeFoods(m.allergen_ingredient_ids),
    avoided: describeFoods(m.avoided_ingredient_ids),
    version: m.version,
    updated_at: iso(m.updatedAt),
  }
}

type MemberFields = Pick<MemberRow, 'display_name' | 'birth_date' | 'equation_sex' | 'weight_kg' | 'activity_category'
  | 'goal' | 'portion' | 'allergen_ingredient_ids' | 'avoided_ingredient_ids'>

function cleanIds(value: unknown, field: string): string[] {
  const list = Array.isArray(value) ? value : []
  const cleaned = [...new Set(list.map((v) => String(v).trim()).filter(Boolean))]
  if (cleaned.length > MAX_RESTRICTIONS) throw invalidInput(`最多可設定 ${MAX_RESTRICTIONS} 項。`, field, 'TOO_MANY_ITEMS')
  if (unknownFoodIds(cleaned).length) {
    throw invalidInput('包含不在標準食材字典中的項目，請從清單選擇。', field, 'UNKNOWN_INGREDIENT_ID')
  }
  return cleaned
}

/** 驗證並正規化成員欄位；任何不合格都回報可修正的欄位與原因。 */
export function parseMember(state: FakeState, raw: unknown): MemberFields {
  const body = asRecord(raw)
  const problems: { field: string; reason: string; message: string }[] = []
  const name = typeof body.display_name === 'string' ? body.display_name.trim() : ''
  if (!name) problems.push({ field: 'display_name', reason: 'value_error', message: '請輸入成員名稱。' })
  else if (name.length > 50) problems.push({ field: 'display_name', reason: 'string_too_long', message: '名稱最多 50 個字。' })
  if (!isDate(body.birth_date)) problems.push({ field: 'birth_date', reason: 'date_parsing', message: '請輸入有效的日期。' })
  if (body.equation_sex !== 'M' && body.equation_sex !== 'F') {
    problems.push({ field: 'equation_sex', reason: 'literal_error', message: '請選擇計算用性別。' })
  }
  const weight = Number(body.weight_kg)
  if (body.weight_kg === undefined || body.weight_kg === null || body.weight_kg === '' || !(weight > 0) || weight > 500) {
    problems.push({ field: 'weight_kg', reason: 'greater_than', message: '體重必須大於 0、不超過 500。' })
  }
  if (!ACTIVITIES.includes(body.activity_category as Activity)) {
    problems.push({ field: 'activity_category', reason: 'literal_error', message: '請選擇活動量。' })
  }
  if (!GOALS.includes(body.goal as Goal)) problems.push({ field: 'goal', reason: 'literal_error', message: '請選擇目標。' })
  if (body.portion === undefined || body.portion === null || !Number.isFinite(Number(body.portion))) {
    problems.push({ field: 'portion', reason: 'decimal_parsing', message: '請選擇食量。' })
  }
  if (problems.length) throw badFields(problems)

  const birth = body.birth_date as string
  if (birth > today(state)) throw invalidInput('出生日期不能晚於今天。', 'birth_date', 'INVALID_DATE')
  const portion = num(Number(body.portion))
  if (!PORTIONS.includes(portion)) throw invalidInput('食量只能選 0.7、0.85、1、1.15 或 1.3。', 'portion', 'INVALID_PORTION')
  const fields: MemberFields = {
    display_name: name,
    birth_date: birth,
    equation_sex: body.equation_sex as 'M' | 'F',
    weight_kg: num(weight),
    activity_category: body.activity_category as Activity,
    goal: body.goal as Goal,
    portion,
    allergen_ingredient_ids: [],
    avoided_ingredient_ids: [],
  }
  try {
    // 確認以此設定算得出每日目標（例如減脂低於 1200 kcal 會被拒絕）
    dailyTarget(fields, today(state))
  } catch (err) {
    if (err instanceof TargetError) throw invalidInput(err.message, err.field, err.reason)
    throw err
  }
  fields.allergen_ingredient_ids = cleanIds(body.allergen_ingredient_ids, 'allergen_ingredient_ids')
  fields.avoided_ingredient_ids = cleanIds(body.avoided_ingredient_ids, 'avoided_ingredient_ids')
  return fields
}

export function householdMembers(state: FakeState, householdId: string): Map<string, MemberRow> {
  return new Map(state.members.filter((m) => m.householdId === householdId).map((m) => [m.id, m]))
}

// ---- 問卷草稿 ----

const NON_DISH_PHRASES = [
  '不知道', '不曉得', '不清楚', '不確定', '隨便', '隨意', '都可以', '都行', '都好', '皆可', '無所謂', '沒意見', '沒想法',
  '你決定', '你選', '幫我選', '看著辦', '看你', '想不到', '不指定', '不用指定', '沒有指定', '未指定', '亂打', '亂填',
]
const NON_DISH_ANSWERS = new Set([
  '沒有', '沒', '無', '否', '不要', '不用', '不需要', '任意', '任何', '什麼', '啥', '都', '好', '可以', '嗯', '喔', '測試',
  'idk', 'none', 'null', 'na', 'no', 'any', 'anything', 'whatever', 'random', 'test', 'asdf', 'qwer',
])
const FILLER = new Set([...'我也都就吧啦啦耶欸誒喔哦呢啊阿嗯呀哈～~ '])

/** 「123」「不知道」「隨便」都不是菜名；是菜名回傳 null。 */
export function notADishName(text: string): string | null {
  if (!/\p{L}/u.test(text)) return '不是菜名，請輸入菜名（不能只有數字或符號）。'
  const letters = [...text.toLowerCase()].filter((ch) => /[\p{L}\p{N}\s]/u.test(ch))
  const core = letters.filter((ch) => !FILLER.has(ch)).join('')
  if (NON_DISH_PHRASES.some((p) => core.includes(p)) || NON_DISH_ANSWERS.has(core) || !core) {
    return '不是菜名；沒有想吃的菜請留空，系統會自動配菜。'
  }
  return null
}

/** 與後端 MealRequestDraft 相同的預設值與格式檢查；「尚未填完」不是錯誤，只列為 issues。 */
export function parseDraft(raw: unknown): MealRequestDraft {
  const body = asRecord(raw)
  const problems: { field: string; reason: string; message: string }[] = []
  const bad = (field: string, reason: string, message: string) => { problems.push({ field, reason, message }) }

  const mode = body.mode ?? 'questionnaire'
  if (mode !== 'questionnaire' && mode !== 'picker') bad('mode', 'literal_error', '模式不正確。')
  const mealDate = body.meal_date ?? null
  if (mealDate !== null && !isDate(mealDate)) bad('meal_date', 'date_parsing', '請輸入有效的日期。')
  const mealType = body.meal_type ?? null
  if (mealType !== null && !MEALS.includes(mealType as MealType)) bad('meal_type', 'literal_error', '餐別不正確。')

  let budget: string | null = null
  if (body.budget !== undefined && body.budget !== null && body.budget !== '') {
    const value = Number(body.budget)
    if (!Number.isFinite(value)) bad('budget', 'decimal_parsing', '預算必須是數字。')
    else if (value <= 0) bad('budget', 'greater_than', '預算必須大於 0。')
    else if (value > 100000) bad('budget', 'less_than_equal', '預算不能超過 100000。')
    else if (Math.round(value * 100) !== value * 100) bad('budget', 'decimal_max_places', '預算最多到小數 2 位。')
    else budget = num(value)
  }
  let staple: string | null = null
  if (typeof body.staple === 'string') {
    if (body.staple.length > 30) bad('staple', 'string_too_long', '主食最多 30 個字。')
    staple = body.staple.trim() || null
  }
  let dishCount: number | null = null
  if (body.dish_count !== undefined && body.dish_count !== null) {
    const value = Number(body.dish_count)
    if (!Number.isInteger(value) || value < 1 || value > MAX_DISHES) bad('dish_count', 'int_range', `菜數必須是 1 到 ${MAX_DISHES}。`)
    else dishCount = value
  }
  const taste = typeof body.taste_preferences === 'string' ? body.taste_preferences.trim() : ''
  if (taste.length > 200) bad('taste_preferences', 'string_too_long', '口味偏好最多 200 個字。')

  const rawDishes = Array.isArray(body.specified_dishes) ? body.specified_dishes : []
  const dishes = [...new Set(rawDishes.map((v) => String(v).trim()).filter(Boolean))]
  if (rawDishes.length > MAX_DISHES) bad('specified_dishes', 'too_long', `指定菜色最多 ${MAX_DISHES} 道。`)
  if (dishes.some((d) => d.length > 50)) bad('specified_dishes', 'value_error', '指定菜色名稱最多 50 個字。')
  for (const dish of dishes) {
    const reason = notADishName(dish)
    if (reason) bad('specified_dishes', 'dish_name_invalid', `指定菜色「${dish}」${reason}`)
  }

  const participants: Participant[] = []
  for (const entry of Array.isArray(body.participants) ? body.participants : []) {
    const p = asRecord(entry)
    if (typeof p.member_id !== 'string' || !p.member_id) {
      bad('participants', 'uuid_parsing', '成員編號不正確。')
      continue
    }
    const bentoMeal = p.bento_meal ?? 'lunch'
    if (!MEALS.includes(bentoMeal as MealType)) bad('participants', 'literal_error', '便當餐別不正確。')
    participants.push({
      member_id: p.member_id,
      dining: p.dining === undefined ? true : Boolean(p.dining),
      bento: Boolean(p.bento),
      bento_meal: bentoMeal as MealType,
    })
  }
  if (participants.length > 20) bad('participants', 'too_long', '最多 20 位成員。')
  if (new Set(participants.map((p) => p.member_id)).size !== participants.length) {
    bad('', 'value_error', '同一位成員只能出現一次；用餐與便當請在同一列勾選。')
  }
  const rawAvailable = Array.isArray(body.available_ingredient_ids) ? body.available_ingredient_ids : []
  if (rawAvailable.length > 200) bad('available_ingredient_ids', 'too_long', '已有食材最多 200 項。')

  if (problems.length) throw badFields(problems)
  return {
    mode: mode as MealRequestDraft['mode'],
    meal_date: mealDate as string | null,
    meal_type: mealType as MealType | null,
    budget,
    staple,
    dish_count: dishCount,
    taste_preferences: taste,
    specified_dishes: dishes,
    participants,
    available_ingredient_ids: [...new Set(rawAvailable.map((v) => String(v).trim()).filter(Boolean))],
  }
}

/** 儲存前必須通過：成員屬於本家庭、食材在標準字典中。 */
export function rejectBadReferences(members: Map<string, MemberRow>, draft: MealRequestDraft): void {
  if (draft.participants.some((p) => !members.has(p.member_id))) {
    throw invalidInput('包含不屬於此家庭的成員。', 'participants', 'UNKNOWN_MEMBER')
  }
  if (unknownFoodIds(draft.available_ingredient_ids).length) {
    throw invalidInput('包含不在標準食材字典中的項目，請從清單選擇。', 'available_ingredient_ids', 'UNKNOWN_INGREDIENT_ID')
  }
}

// ---- 主食 ----

const STAPLE_LABELS = ['白飯', '糙米飯']
export const STAPLE_AUTO = '不指定'
const STAPLE_NONE_VALUES = ['不需要主食', '不需要', '無', '沒有', '不用', '免', '不要', '免了', '不需', '不吃', '不必']

export function stapleOptions(): StapleOptions {
  return {
    options: [
      { label: '白飯', aliases: ['飯', '米飯'] },
      { label: '糙米飯', aliases: [] },
      { label: STAPLE_AUTO, aliases: [] },
    ],
    none_value: STAPLE_NONE_VALUES[0],
    none_values: STAPLE_NONE_VALUES,
    config_version: 'fake-staples-1',
  }
}

export function isNoStaple(text: string | null): boolean {
  if (!text) return true
  return STAPLE_NONE_VALUES.includes(text) || (text.includes('主食') && /[不沒無免]/.test(text))
}
/** 問卷選的主食標籤（白飯／糙米飯）；不需要主食回傳 null。 */
export function stapleLabel(text: string | null): string | null {
  if (isNoStaple(text)) return null
  if (text === STAPLE_AUTO) return '白飯'
  if (text === '飯' || text === '米飯') return '白飯'
  return STAPLE_LABELS.includes(text!) ? text : null
}

function stapleIssue(text: string | null): Issue | null {
  if (text === null || isNoStaple(text) || stapleLabel(text)) return null
  return { field: 'staple', reason: 'STAPLE_NOT_SUPPORTED',
    message: `主食「${text}」不在可選清單中，請從清單選擇（不需要主食也請選「不需要主食」）。` }
}

// ---- 完整性、限制與回應 ----

export function completeness(draft: MealRequestDraft, members: Map<string, MemberRow>): Issue[] {
  const issues: Issue[] = []
  let required: [keyof MealRequestDraft, string][] = [
    ['meal_date', '請選擇餐食日期。'],
    ['meal_type', '請選擇餐別。'],
    ['budget', '請輸入預算。'],
    ['staple', '請輸入主食（不需要主食也請註明）。'],
    ['dish_count', '請選擇菜數。'],
  ]
  if (draft.mode === 'picker') {
    // 食譜庫選菜不問預算，但一定要挑菜
    required = required.filter(([field]) => field !== 'budget')
    if (!draft.specified_dishes.length) {
      issues.push({ field: 'specified_dishes', reason: 'REQUIRED', message: '請從食譜庫至少挑一道菜。' })
    }
  }
  for (const [field, message] of required) {
    if (draft[field] === null) issues.push({ field, reason: 'REQUIRED', message })
  }
  const staple = stapleIssue(draft.staple)
  if (staple) issues.push(staple)
  if (!requestItems(draft).length) {
    issues.push({ field: 'participants', reason: 'REQUIRED', message: '請至少選擇一位用餐或帶便當的成員。' })
  }
  if (draft.participants.some((p) => !members.has(p.member_id))) {
    issues.push({ field: 'participants', reason: 'UNKNOWN_MEMBER', message: '有成員已被刪除，請重新選擇。' })
  }
  if (draft.dish_count !== null && draft.specified_dishes.length > draft.dish_count) {
    issues.push({ field: 'specified_dishes', reason: 'TOO_MANY_SPECIFIED_DISHES',
      message: `指定菜色有 ${draft.specified_dishes.length} 道，超過菜數 ${draft.dish_count}；指定菜會計入菜數，請增加菜數或減少指定菜。` })
  }
  return issues
}

/** 必要限制取本次用餐與便當成員的聯集，並標出來自哪些成員。 */
export function restrictions(draft: MealRequestDraft, members: Map<string, MemberRow>): MealRequest['restrictions'] {
  type Kind = 'allergen' | 'avoided'
  const union = new Map<string, { ingredient_id: string; name: string | null; kinds: Kind[]; member_names: string[] }>()
  for (const p of draft.participants) {
    const member = members.get(p.member_id)
    if (!member || !(p.bento || (p.dining && draft.meal_type))) continue
    const groups: [Kind, string[]][] = [['allergen', member.allergen_ingredient_ids], ['avoided', member.avoided_ingredient_ids]]
    for (const [kind, ids] of groups) {
      for (const id of ids) {
        const row = union.get(id) ?? { ingredient_id: id, name: food(id)?.name ?? null, kinds: [], member_names: [] }
        if (!row.kinds.includes(kind)) row.kinds.push(kind)
        if (!row.member_names.includes(member.display_name)) row.member_names.push(member.display_name)
        union.set(id, row)
      }
    }
  }
  // 同義食材也一併排除，例如對「白蝦」過敏也排除「蝦仁」
  const expanded = new Map<string, MealRequest['restrictions']['expanded_ingredients'][number]>()
  for (const [baseId, base] of union) {
    for (const extra of expansions(baseId)) {
      if (union.has(extra.food_id)) continue
      const row = expanded.get(extra.food_id) ?? {
        ingredient_id: extra.food_id, name: food(extra.food_id)?.name ?? null, relation: extra.relation, via: [],
        kinds: [], member_names: [],
      }
      if (!row.via.includes(baseId)) row.via.push(baseId)
      for (const kind of base.kinds) if (!row.kinds.includes(kind)) row.kinds.push(kind)
      for (const name of base.member_names) if (!row.member_names.includes(name)) row.member_names.push(name)
      expanded.set(extra.food_id, row)
    }
  }
  const excluded = new Set([...union.keys(), ...expanded.keys()])
  return {
    ingredients: [...union.values()],
    expanded_ingredients: [...expanded.values()],
    excluded_food_ids: [...excluded].sort(),
    available_but_restricted: draft.available_ingredient_ids.filter((id) => excluded.has(id)),
  }
}

/** 假的預算下限：每道菜每份約 12 元。菜數或目標未定時不估算。 */
function budgetFloor(draft: MealRequestDraft, targets: Targets): number | null {
  if (draft.dish_count === null || targets.status !== 'available') return null
  return Math.round(draft.dish_count * targets.totalN * 12)
}

export function checkRequest(draft: MealRequestDraft, members: Map<string, MemberRow>) {
  const issues = completeness(draft, members)
  const targets = computeTargets(draft, members)
  const floor = budgetFloor(draft, targets)
  if (draft.budget !== null && floor !== null && Number(draft.budget) < floor) {
    issues.push({ field: 'budget', reason: 'BUDGET_TOO_LOW',
      message: `預算 ${draft.budget} 元低於這一餐估算的最低金額約 ${floor} 元（${draft.dish_count} 道菜），請調高預算後再送出。` })
  }
  return { issues, targets, floor }
}

export function requestView(state: FakeState, row: RequestRow): MealRequest {
  const members = householdMembers(state, row.householdId)
  const { issues, targets, floor } = checkRequest(row.draft, members)
  return {
    id: row.id,
    status: row.status,
    version: row.version,
    updated_at: iso(row.updatedAt),
    draft: row.draft,
    available_ingredients: describeFoods(row.draft.available_ingredient_ids),
    items: requestItems(row.draft),
    restrictions: restrictions(row.draft, members),
    targets_preview: targetsView(targets),
    budget_floor: floor === null ? null : String(floor),
    issues,
    complete: issues.length === 0,
  }
}

export function requestSummary(row: RequestRow): MealRequestSummary {
  return {
    id: row.id, status: row.status, version: row.version, meal_date: row.draft.meal_date,
    meal_type: row.draft.meal_type, mode: row.draft.mode, updated_at: iso(row.updatedAt),
  }
}

export function ownedRequest(state: FakeState, householdId: string, id: string, message = '找不到此問卷。'): RequestRow {
  const row = state.requests.find((r) => r.id === id && r.householdId === householdId)
  if (!row) throw new ApiFail(404, 'NOT_FOUND', message)
  return row
}

/** 問卷必須填完才能查菜單、算營養。 */
export function requireComplete(draft: MealRequestDraft, members: Map<string, MemberRow>): void {
  rejectBadReferences(members, draft)
  const issues = completeness(draft, members)
  if (issues.length) throw new ApiFail(422, 'INVALID_INPUT', '請先完成問卷。', { issues })
}
