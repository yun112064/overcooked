import type {
  Activity, DecisionKind, Goal, Job, MealRequestDraft, MealResult, MealType, NutritionPreview, RecipeSummary,
} from '../../src/api/types.ts'

/**
 * 假後端的共用型別與小工具。
 * 所有處理函式都是「狀態物件 + 請求」的純函式，不需要真的 HTTP 伺服器就能呼叫（測試直接呼叫）。
 */

/** 與 backend 的 ApiError 相同：狀態碼 + 穩定錯誤碼 + 給人看的訊息 + details。 */
export class ApiFail extends Error {
  status: number
  code: string
  details: Record<string, unknown>
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

export const notFound = (message = '找不到資料。') => new ApiFail(404, 'NOT_FOUND', message)
export const versionConflict = () => new ApiFail(409, 'VERSION_CONFLICT', '資料已被更新，請重新載入後再修改。')
export const alreadyAdopted = () =>
  new ApiFail(409, 'VERSION_CONFLICT', '此問卷已採用，不能再生成或修改；請另建新問卷。', { reason: 'ALREADY_ADOPTED' })
export function invalidInput(message: string, field?: string, reason?: string) {
  const details: Record<string, unknown> = {}
  if (field) details.field = field
  if (reason) details.reason = reason
  return new ApiFail(422, 'INVALID_INPUT', message, details)
}
/** FastAPI 的請求格式錯誤：422 + details.fields。 */
export function badFields(fields: { field: string; reason: string; message: string }[]) {
  return new ApiFail(422, 'INVALID_INPUT', '輸入資料格式不正確。', { fields })
}

export type FakeRequest = {
  method: string
  /** 不含 /api 前綴，例如 /meal-requests/abc */
  path: string
  query: Record<string, string>
  /** 標頭名稱一律小寫 */
  headers: Record<string, string>
  body: unknown
  /** 用來組假超市的絕對網址，例如 http://localhost:5174 */
  origin?: string
  /** multipart 上傳時的檔名（辨識用） */
  filename?: string
}

export type SseEvent = { event: string; data: unknown }

export type FakeResponse = {
  status: number
  body?: unknown
  /** 串流回應（飲食小幫手）；中介層會一段一段送出 */
  sse?: SseEvent[]
  /** 非 JSON 的內容（辨識照片的示意圖） */
  raw?: { contentType: string; text: string }
}

// ---- 列（資料表的一筆） ----

export type UserRow = { id: string; email: string; householdIds: string[] }
/** seeded：種子家庭，沒看過的 Email 登入時直接是它的成員 */
export type HouseholdRow = { id: string; name: string; createdAt: number; seeded: boolean }

export type MemberRow = {
  id: string
  householdId: string
  display_name: string
  birth_date: string
  equation_sex: 'M' | 'F'
  weight_kg: string
  activity_category: Activity
  goal: Goal
  portion: string
  allergen_ingredient_ids: string[]
  avoided_ingredient_ids: string[]
  version: number
  createdAt: number
  updatedAt: number
}

export type RequestRow = {
  id: string
  householdId: string
  status: string
  version: number
  draft: MealRequestDraft
  createdAt: number
  updatedAt: number
}

export type JobRow = {
  id: string
  householdId: string
  kind: 'meal_generation' | 'knowledge_retry' | 'price_retry'
  status: Job['status']
  stage: string | null
  attempts: number
  requestId: string
  inputVersion: number
  resultVersion: number | null
  sourceResultId: string | null
  sourceResultVersion: number | null
  error: { code: string; message: string } | null
  resultSummary: Job['result_summary']
  idempotencyKey: string
  createdAt: number
  updatedAt: number
  /** 假的時間表：排隊多久、執行多久、最後成功或失敗 */
  queuedMs: number
  runMs: number
  stages: string[]
  failure: { code: string; message: string } | null
}

/** 結果中不隨時間改變的部分；採用資格、重試狀態、問卷狀態在讀取時才算。 */
export type ResultBody = Pick<MealResult,
  'sodium_adjustment' | 'dishes' | 'staple' | 'nutrition' | 'prices' | 'knowledge' | 'previous_day'
  | 'knowledge_retry' | 'price_retry'> & {
  /** 後端 calculation.block_reasons 的代碼（NUTRITION_INCOMPLETE、NUTRITION_OUT_OF_RANGE…） */
  blockCodes: string[]
  /** 問卷口味裡的測試暗號，重試時要沿用 */
  flags: string[]
  targets: { rule_version: string; total_n: string; target: { energy_kcal: string; protein_g: string } }
  requestSnapshot: MealRequestDraft
}

export type ResultRow = {
  id: string
  requestId: string | null
  householdId: string
  resultVersion: number
  requestVersion: number
  derivedFromVersion: number | null
  createdAt: number
  mode: MealResult['mode']
  fallbackReason: string | null
  body: ResultBody
}

export type DecisionRow = {
  id: string
  householdId: string
  resultId: string
  kind: DecisionKind
  resultVersion: number
  requestVersion: number
  missingFoodIds: string[] | null
  idempotencyKey: string
  createdAt: number
}

/** 資料庫菜單（食譜庫選菜）採用時保存的快照。 */
export type PreviewSnapshot = {
  selected: RecipeSummary[]
  staple_choice: string | null
  status: NutritionPreview['status']
  status_message: string
  targets: Record<string, string>
  nutrition: { totals: Record<string, string | null> | null }
  nutrition_all: NutritionPreview['nutrition_all']
  dishes: NutritionPreview['dishes']
  staple: NutritionPreview['staple']
  people: NutritionPreview['people']
  daily_reference_meta: NutritionPreview['daily_reference_meta']
  market_shortages?: {
    items: { food_id: string; name: string; shortage_grams: string | null }[]
    unquantified_items: { food_id: string; name: string }[]
  }
}

export type PlanDish = {
  standard_dish_id: string
  dish_name: string
  aliases: string[]
  source: 'database' | 'ai'
  recipe_id: string | null
}

export type PlanRow = {
  id: string
  householdId: string
  /** 種子資料用「幾天前」，查詢時才換算成日期，所以「今天」永遠跟著系統時鐘走 */
  date: string | { daysAgo: number }
  mealType: MealType
  mode: MealResult['mode']
  status: 'adopted'
  adoptedAt: number
  requestId: string | null
  resultVersion: number | null
  requestVersion: number | null
  priceExceptionAccepted: boolean
  idempotencyKey: string
  /** 冪等比對用：同一把鍵必須是同一個命令 */
  commandHash: string
  dishes: PlanDish[]
  /** 生成結果採用：結果快照 */
  result: { body: ResultBody; fallbackReason: string | null; historyChanged: boolean } | null
  /** 資料庫菜單採用：預覽快照 */
  preview: PreviewSnapshot | null
}

export type RecognitionRow = {
  id: string
  householdId: string
  requestId: string | null
  status: 'succeeded' | 'no_result' | 'unavailable'
  reason: string | null
  suggestions: { name: string; ingredient_id: string | null }[]
  confirmed: { name: string; ingredient_id: string }[]
}

export type TeamService = { status: 'online' | 'offline' | 'not_configured'; reason: string | null }
export type TeamServiceName = 'recognition' | 'knowledge' | 'assistant' | 'assistant_openai'

export type FakeState = {
  /** 可注入的時鐘（毫秒）；測試用假時鐘推進工作進度 */
  now: () => number
  /** 種子資料把時鐘撥回過去時固定住「今天」，相對日期才不會跟著跑掉 */
  fixedToday: string | null
  seq: number
  users: UserRow[]
  /** 單一瀏覽器工作階段：假後端不發 Cookie */
  session: { userId: string; householdId: string | null } | null
  households: HouseholdRow[]
  members: MemberRow[]
  requests: RequestRow[]
  jobs: JobRow[]
  results: ResultRow[]
  decisions: DecisionRow[]
  rejections: { requestId: string; version: number; recipeIds: string[] }[]
  plans: PlanRow[]
  recognitions: RecognitionRow[]
  team: Record<TeamServiceName, TeamService>
}

const ID_PREFIX = {
  user: '80', household: '10', member: '20', request: '30', job: '40', result: '45', decision: '50', plan: '60',
  recognition: '70',
}

/** 看起來像 UUID、但每次重啟都一樣的編號（方便截圖比對與測試）。 */
export function newId(state: FakeState, kind: keyof typeof ID_PREFIX): string {
  state.seq += 1
  return `${ID_PREFIX[kind]}000000-0000-4000-8000-${String(state.seq).padStart(12, '0')}`
}

// ---- 數字與日期 ----

/** 顯示用的精確字串：取到小數 2 位，去掉多餘的 0。 */
export function num(value: number): string {
  return String(Math.round(value * 100) / 100)
}
/** 比例（份量占比、調整倍率）多留幾位，加總才會接近 1。 */
export function ratio(value: number): string {
  return String(Math.round(value * 10000) / 10000)
}
/** null 就是未知，絕不變成 0。 */
export function numOrNull(value: number | null): string | null {
  return value === null ? null : num(value)
}

const pad = (n: number) => String(n).padStart(2, '0')

/** 本機日期（與前端 localToday() 相同的算法）。 */
export function localDate(ms: number): string {
  const d = new Date(ms)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
export function addDays(date: string, days: number): string {
  const [y, m, d] = date.split('-').map(Number)
  const next = new Date(y, m - 1, d + days)
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}`
}
export function isDate(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [y, m, d] = value.split('-').map(Number)
  const parsed = new Date(y, m - 1, d)
  return parsed.getFullYear() === y && parsed.getMonth() === m - 1 && parsed.getDate() === d
}
export const iso = (ms: number) => new Date(ms).toISOString()

/** 足歲：生日當天才加一歲。 */
export function ageOn(birth: string, on: string): number {
  const [by, bm, bd] = birth.split('-').map(Number)
  const [y, m, d] = on.split('-').map(Number)
  return y - by - (m < bm || (m === bm && d < bd) ? 1 : 0)
}

export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
