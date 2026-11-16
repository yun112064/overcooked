/**
 * 鍋狀態機（DESIGN.md 5.5）。同一口鍋出現在客廳門口（遠景）、廚房瓦斯爐（側視）、單板旁（俯視），
 * 三個地方都用這裡的 potState() 決定它長什麼樣子，再交給 <Pot> 畫。純函式，不碰網路。
 *
 *   const state = potState({ request, job })              // 廚房、客廳：照資料推算
 *   const state = potState({ request, job, stepsDone })   // 單板：草稿階段由問卷自己說填到第幾步
 *   const { left, right } = stovePots(requests, jobs)     // 兩口爐各放哪一口鍋
 *
 * 六種狀態：
 *   empty    空鍋：沒有進行中的問卷（或草稿第一步還沒填完、已採用）
 *   filling  半滿：草稿，steps＝已完成的步數 1–4（菜→菇→肉→湯）
 *   full     全滿：五步都好了，等送出
 *   cooking  蓋上冒煙：工作 queued／running
 *   ready    開蓋全亮：有結果（result_ready／awaiting_decision）
 *   failed   失敗：鍋蓋半開冒黑煙
 *
 * 判斷順序（和舊前端 GenerationPanel 一樣，工作比問卷新）：
 *   1. 沒有問卷、或已採用（confirmed）→ 空鍋。
 *   2. 這份問卷有生成工作在跑 → cooking。
 *   3. 這一版問卷的生成工作已結束 → succeeded＝ready；failed＝failed；
 *      使用者自己取消（error.code 'CANCELLED'）不算失敗，回到問卷本身的狀態。
 *   4. 其餘看問卷的 status。草稿依已完成步數決定半滿或全滿。
 *
 * 鍋裡的料只是進度記號（第幾步就多哪一樣），和使用者填了什麼內容無關。
 */
import type { Issue, Job, MealRequestDraft } from '../api/types'

/** 問卷共五步（DESIGN.md 5.4）；前四步各加一樣料，第五步補滿。 */
export const POT_STEPS = 5

export type PotState =
  | { kind: 'empty' }
  | { kind: 'filling'; steps: 1 | 2 | 3 | 4 }
  | { kind: 'full' }
  | { kind: 'cooking' }
  | { kind: 'ready' }
  | { kind: 'failed' }

export type PotKind = PotState['kind']

/**
 * 判斷鍋的狀態需要的問卷欄位。MealRequest（完整）和 MealRequestSummary（清單）都可以直接傳進來。
 * 清單沒有 issues／complete，草稿只能粗略判斷（見 draftSteps）。
 */
export type PotRequest = {
  id: string
  status: string
  version: number
  /** 完整問卷才有 */
  complete?: boolean
  issues?: readonly Issue[]
  draft?: Pick<MealRequestDraft, 'available_ingredient_ids'>
  /** 清單摘要才有 */
  meal_date?: string | null
  meal_type?: string | null
  /** 'picker'＝從食譜書挑菜的單（不是五步問卷） */
  mode?: string
}

export type PotJob = Pick<Job, 'kind' | 'status' | 'meal_request_id' | 'input_version' | 'error'>

/** 只有生成工作會改變鍋的狀態；小知識重試、查價重試不算。 */
const GENERATION_KIND = 'meal_generation'
const CANCELLED = 'CANCELLED'

/**
 * 各步要補齊的欄位（後端 issues 的 field），依 DESIGN.md 5.4 的順序。
 * 第 3 步（現有食材辨識）可以整步略過，沒有必填欄位；單板調整步驟內容時這裡要一起改。
 */
export const POT_STEP_FIELDS = {
  1: ['meal_date', 'meal_type', 'budget'],
  2: ['participants'],
  4: ['staple', 'dish_count', 'specified_dishes'],
} as const

/** 已經送去生成過的鍋（不是草稿階段） */
const SUBMITTED_KINDS: ReadonlySet<PotKind> = new Set(['cooking', 'ready', 'failed'])

const EMPTY: PotState = { kind: 'empty' }
const FULL: PotState = { kind: 'full' }
const COOKING: PotState = { kind: 'cooking' }
const READY: PotState = { kind: 'ready' }
const FAILED: PotState = { kind: 'failed' }

/** 已完成步數 → 草稿的鍋：0 空鍋、1–4 半滿、5 以上全滿。 */
export function potFromSteps(stepsDone: number): PotState {
  const steps = Number.isFinite(stepsDone) ? Math.floor(stepsDone) : 0
  if (steps <= 0) return EMPTY
  if (steps >= POT_STEPS) return FULL
  return { kind: 'filling', steps: steps as 1 | 2 | 3 | 4 }
}

/**
 * 從問卷資料推算草稿「連續填好了幾步」（0–4；五步都好由 complete 判斷）。
 * 只看「還沒填」（reason 為 REQUIRED）：填了但不合格（例如預算太低）仍算填過，料留在鍋裡，只是不會變成全滿。
 * 第 3 步沒有必填欄位，要有「走過」的證據才算：選了現有食材，或第 4 步已經填好。
 * 清單摘要沒有 issues：餐期與餐別都有就當作至少第一步，否則 0。
 */
export function draftSteps(request: PotRequest): 0 | 1 | 2 | 3 | 4 {
  if (!request.issues) return request.meal_date && request.meal_type ? 1 : 0
  const missing = new Set(request.issues.filter((issue) => issue.reason === 'REQUIRED').map((issue) => issue.field))
  const filled = (fields: readonly string[]) => fields.every((field) => !missing.has(field))
  if (!filled(POT_STEP_FIELDS[1])) return 0
  if (!filled(POT_STEP_FIELDS[2])) return 1
  const fourth = filled(POT_STEP_FIELDS[4])
  const pickedIngredients = (request.draft?.available_ingredient_ids.length ?? 0) > 0
  if (!fourth) return pickedIngredients ? 3 : 2
  return 4
}

function draftPot(request: PotRequest, stepsDone: number | undefined, submitted: boolean): PotState {
  if (stepsDone !== undefined) return potFromSteps(stepsDone)
  // 送出過的問卷一定是填完整的
  if (submitted || request.complete) return FULL
  return potFromSteps(draftSteps(request))
}

type PotInput = {
  request: PotRequest | null | undefined
  /** 這份問卷最新的生成工作（沒有或還沒讀到就不傳） */
  job?: PotJob | null
  /** 單板用：問卷目前完成到第幾步（0–5）。只影響草稿階段；不傳就從問卷資料推算 */
  stepsDone?: number
}

export function potState({ request, job, stepsDone }: PotInput): PotState {
  if (!request || request.status === 'confirmed') return EMPTY

  const own = job && job.kind === GENERATION_KIND && job.meal_request_id === request.id ? job : null
  if (own && (own.status === 'queued' || own.status === 'running')) return COOKING

  const submitted = request.status === 'queued' || request.status === 'running'
  if (own && own.input_version === request.version) {
    if (own.status === 'succeeded') return READY
    if (own.error?.code !== CANCELLED) return FAILED
    // 取消：問卷若還停在「生成中」只是還沒重新讀取，先當成回到待送出（料保留）
    if (submitted) return draftPot(request, stepsDone, true)
  }

  if (submitted) return COOKING
  if (request.status === 'result_ready' || request.status === 'awaiting_decision') return READY
  if (request.status === 'failed') return FAILED
  return draftPot(request, stepsDone, false)
}

/** 給物件列、讀屏用的狀態文字。 */
export function potLabel(state: PotState): string {
  switch (state.kind) {
    case 'empty': return '空鍋'
    case 'filling': return `備料中 ${state.steps}/${POT_STEPS}`
    case 'full': return '可以開始煮'
    case 'cooking': return '生成中'
    case 'ready': return '煮好了'
    case 'failed': return '失敗'
  }
}

/** 從工作清單（新到舊）找出某份問卷最新的生成工作。 */
export function latestGenerationJob<J extends PotJob>(jobs: readonly J[], requestId: string): J | null {
  return jobs.find((job) => job.kind === GENERATION_KIND && job.meal_request_id === requestId) ?? null
}

export type StovePot<R extends PotRequest> = {
  /** 這口鍋對應的問卷；null＝備用空鍋（點它是開新的一餐） */
  request: R | null
  state: PotState
}

/**
 * 兩口爐各放什麼（DESIGN.md 5.2；客廳門口的遠景也用同一份）。
 * requests 是 GET /meal-requests 的清單（新到舊），jobs 是 GET /jobs 的清單（新到舊，可不傳）。
 *
 * - 左爐口：最新一份還沒採用的問卷；都沒有就是空鍋（request 為 null）。
 * - 右爐口：另外還有一份在生成中 → 第二口蓋著的鍋；否則左鍋不是空鍋時放一口備用空鍋；左鍋是空鍋時不放（null）。
 * - 第三份以上只掛在單軌，不上爐。
 * - 食譜書挑菜的單（mode 'picker'）還在挑的時候不上爐：鍋裡的料代表問卷五步的進度，挑菜沒有這五步。
 *   送去生成之後（在煮、煮好、失敗）才和問卷一樣上爐。
 */
export function stovePots<R extends PotRequest>(
  requests: readonly R[], jobs: readonly PotJob[] = [],
): { left: StovePot<R>; right: StovePot<R> | null } {
  const pots = requests
    .filter((request) => request.status !== 'confirmed')
    .map((request) => ({ request, state: potState({ request, job: latestGenerationJob(jobs, request.id) }) }))
    .filter((pot) => pot.request.mode !== 'picker' || SUBMITTED_KINDS.has(pot.state.kind))
  const [first, ...rest] = pots
  if (!first) return { left: { request: null, state: EMPTY }, right: null }
  const second = rest.find((pot) => pot.state.kind === 'cooking')
  if (second) return { left: first, right: second }
  return { left: first, right: first.state.kind === 'empty' ? null : { request: null, state: EMPTY } }
}
