import { Readable } from 'node:stream'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { beforeEach, describe, expect, it } from 'vitest'
import type {
  Job, KnowledgeTips, MealPlanSummary, MealRequest, MealRequestSummary, MealResult, MealResultSummary, Member,
  NutritionPreview, RecipeCatalog, RecipeRetrieval,
} from '../src/api/types.ts'
import { fakeApi, parseMultipart } from './fakeApi.ts'
import { flagsOf } from './fake/results.ts'
import { notADishName } from './fake/requests.ts'
import type { FakeResponse, FakeState } from './fake/core.ts'
import { addDays, localDate } from './fake/core.ts'
import { DEMO_TAKEN_EMAIL, WRONG_PASSWORD, handle } from './fake/router.ts'
import { createState } from './fake/seed.ts'

// 假時鐘：測試自己決定現在幾點，工作進度就能快轉
let clock = 0
let state: FakeState
let keySeq = 0

const START = new Date(2026, 10, 14, 12, 0, 0).getTime()
const tick = (seconds: number) => { clock += seconds * 1000 }
const key = () => `test-key-${String(++keySeq).padStart(4, '0')}`

function call(method: string, path: string, body: unknown = {}, headers: Record<string, string> = {}, extra: { query?: Record<string, string>; filename?: string } = {}): FakeResponse {
  return handle(state, { method, path, query: extra.query ?? {}, headers, body, filename: extra.filename, origin: 'http://localhost:5174' })
}
const get = <T>(path: string, query: Record<string, string> = {}) => {
  const res = call('GET', path, {}, {}, { query })
  expect(res.status, JSON.stringify(res.body)).toBe(200)
  return res.body as T
}
const errorCode = (res: FakeResponse) => (res.body as { error: { code: string; message: string; details: Record<string, unknown> } }).error

function login(household: string | null = '林家廚房') {
  expect(call('POST', '/auth/login', { email: 'demo@example.com', password: 'password123' }).status).toBe(200)
  if (!household) return
  const list = get<{ households: { id: string; name: string }[] }>('/household')
  const target = list.households.find((h) => h.name === household)!
  expect(call('POST', '/household/select', { household_id: target.id }).status).toBe(200)
}

const requests = () => get<{ meal_requests: MealRequestSummary[] }>('/meal-requests').meal_requests
const seeded = (status: string, mode = 'questionnaire') => requests().find((r) => r.status === status && r.mode === mode)!
const latestResult = (id: string) => {
  const version = get<{ results: MealResultSummary[] }>(`/meal-requests/${id}/results`).results[0].result_version
  return get<MealResult>(`/meal-requests/${id}/results/${version}`)
}
const jobsOf = (id: string, kind = 'meal_generation') => get<{ jobs: Job[] }>('/jobs', { meal_request_id: id, kind }).jobs

/** 建立一份填好的問卷並送出，回傳問卷與工作。 */
function submitNew(taste = '', overrides: Record<string, unknown> = {}) {
  const members = get<{ members: Member[] }>('/members').members
  const created = call('POST', '/meal-requests', { meal_date: addDays(localDate(clock), 5) }).body as MealRequest
  const draft = {
    ...created.draft, meal_type: 'dinner', budget: '600', staple: '白飯', dish_count: 3, taste_preferences: taste,
    participants: members.slice(0, 3).map((m) => ({ member_id: m.id, dining: true, bento: false, bento_meal: 'lunch' })),
    ...overrides,
  }
  const saved = call('PUT', `/meal-requests/${created.id}`, { version: 1, draft })
  expect(saved.status, JSON.stringify(saved.body)).toBe(200)
  const request = saved.body as MealRequest
  expect(request.complete, JSON.stringify(request.issues)).toBe(true)
  const submitted = call('POST', `/meal-requests/${request.id}/submit`, { version: request.version }, { 'idempotency-key': key() })
  expect(submitted.status).toBe(202)
  return { request, job: (submitted.body as { job: Job }).job }
}

beforeEach(() => {
  clock = START
  keySeq = 0
  state = createState({ now: () => clock })
})

describe('登入與家庭', () => {
  it('沒登入一律 401；wrongpass1 登入失敗', () => {
    expect(call('GET', '/auth/me').status).toBe(401)
    expect(call('GET', '/members').status).toBe(401)
    const res = call('POST', '/auth/login', { email: 'a@example.com', password: WRONG_PASSWORD })
    expect(res.status).toBe(401)
    expect(errorCode(res).code).toBe('UNAUTHENTICATED')
  })

  it('登入後有兩個種子家庭，沒選家庭前家庭資料回 409', () => {
    login(null)
    const me = get<{ current_household: unknown }>('/auth/me')
    expect(me.current_household).toBeNull()
    const list = get<{ households: { name: string }[] }>('/household')
    expect(list.households.map((h) => h.name)).toEqual(['林家廚房', '外婆家'])
    const res = call('GET', '/members')
    expect(res.status).toBe(409)
    expect(errorCode(res).code).toBe('HOUSEHOLD_NOT_SELECTED')
  })

  it('註冊：已註冊的 Email 回 409；新帳號沒有家庭，新建的家庭是空的', () => {
    const taken = call('POST', '/auth/register', { email: DEMO_TAKEN_EMAIL, password: 'password123' })
    expect(taken.status).toBe(409)
    expect(errorCode(taken).details.reason).toBe('EMAIL_TAKEN')
    expect(call('POST', '/auth/register', { email: 'new@example.com', password: 'short' }).status).toBe(422)
    expect(call('POST', '/auth/register', { email: 'new@example.com', password: 'password123' }).status).toBe(201)
    expect(get<{ households: unknown[] }>('/household').households).toEqual([])
    expect(call('POST', '/household', { name: '  ' }).status).toBe(422)
    expect(call('POST', '/household', { name: '新家' }).status).toBe(201)
    expect(get<{ members: unknown[] }>('/members').members).toEqual([])
    expect(requests()).toEqual([])
    expect(get<{ dates: unknown[] }>('/meal-plans').dates).toEqual([])
    // 只有一個家庭：重新登入會直接選好
    call('POST', '/auth/logout')
    const again = call('POST', '/auth/login', { email: 'new@example.com', password: 'password123' })
    expect((again.body as { current_household: { name: string } }).current_household.name).toBe('新家')
  })
})

describe('成員', () => {
  beforeEach(() => login())

  it('林家 4 人、外婆家 2 人；阿嬤的設定算不出營養目標', () => {
    const members = get<{ members: Member[] }>('/members').members
    expect(members.map((m) => m.display_name)).toEqual(['阿明', '小惠', '小安', '阿嬤'])
    const draft = get<MealRequest>(`/meal-requests/${requests().find((r) => r.status === 'draft' && r.mode === 'questionnaire')!.id}`)
    expect(draft.complete).toBe(false)
    expect(draft.targets_preview.status).toBe('unavailable')
    // 把阿嬤的目標改掉之後就算得出來（同一份問卷不用動）
    const grandma = members[3]
    const body = { display_name: grandma.display_name, birth_date: grandma.birth_date, equation_sex: grandma.equation_sex,
      weight_kg: grandma.weight_kg, activity_category: grandma.activity_category, goal: 'general', portion: grandma.portion,
      allergen_ingredient_ids: [], avoided_ingredient_ids: [] }
    expect(call('PUT', `/members/${grandma.id}`, { ...body, version: 1 }).status).toBe(200)
    expect(get<MealRequest>(`/meal-requests/${draft.id}`).targets_preview.status).toBe('available')
    // 舊版本再存一次：版本衝突
    expect(call('PUT', `/members/${grandma.id}`, { ...body, version: 1 }).status).toBe(409)
    login('外婆家')
    expect(get<{ members: Member[] }>('/members').members).toHaveLength(2)
  })

  it('新增成員會檢查規則：未成年不能選減脂、食量只能選清單上的值', () => {
    const base = { display_name: '小寶', birth_date: '2020-05-05', equation_sex: 'F', weight_kg: '20', activity_category: 'active',
      goal: 'general', portion: '1', allergen_ingredient_ids: [], avoided_ingredient_ids: [] }
    const minor = call('POST', '/members', { ...base, goal: 'fat_loss' })
    expect(minor.status).toBe(422)
    expect(errorCode(minor).details.reason).toBe('GOAL_NOT_ALLOWED_FOR_AGE')
    expect(errorCode(call('POST', '/members', { ...base, portion: '2' })).details.reason).toBe('INVALID_PORTION')
    expect(errorCode(call('POST', '/members', { ...base, allergen_ingredient_ids: ['NOPE'] })).details.reason).toBe('UNKNOWN_INGREDIENT_ID')
    const created = call('POST', '/members', base)
    expect(created.status).toBe(201)
    const id = (created.body as Member).id
    expect(call('DELETE', `/members/${id}`).status).toBe(204)
    expect(call('GET', `/members/${id}`).status).toBe(404)
  })
})

describe('主要流程：問卷 → 生成 → 結果 → 採用 → 已採用紀錄', () => {
  beforeEach(() => login())

  it('建立、儲存（版本衝突）、送出（冪等）、工作依時間推進、採用後出現在已採用紀錄', () => {
    const members = get<{ members: Member[] }>('/members').members
    const date = addDays(localDate(clock), 5)
    const created = call('POST', '/meal-requests', { meal_date: date })
    expect(created.status).toBe(201)
    const first = created.body as MealRequest
    expect(first.complete).toBe(false)
    expect(first.issues.map((i) => i.field)).toContain('budget')

    const draft = { ...first.draft, meal_type: 'dinner', budget: '600', staple: '白飯', dish_count: 3,
      participants: members.slice(0, 3).map((m) => ({ member_id: m.id, dining: true, bento: false, bento_meal: 'lunch' })) }
    const saved = call('PUT', `/meal-requests/${first.id}`, { version: 1, draft }).body as MealRequest
    expect(saved.version).toBe(2)
    expect(saved.complete).toBe(true)
    expect(saved.targets_preview.status).toBe('available')
    // 用舊版本再存不同內容：409
    const stale = call('PUT', `/meal-requests/${first.id}`, { version: 1, draft: { ...draft, dish_count: 4 } })
    expect(stale.status).toBe(409)
    expect(errorCode(stale).code).toBe('VERSION_CONFLICT')
    // 內容沒變：不升版
    expect((call('PUT', `/meal-requests/${first.id}`, { version: 2, draft }).body as MealRequest).version).toBe(2)

    expect(call('POST', `/meal-requests/${first.id}/submit`, { version: 2 }).status).toBe(422) // 沒帶 Idempotency-Key
    const submitKey = key()
    const submitted = call('POST', `/meal-requests/${first.id}/submit`, { version: 2 }, { 'idempotency-key': submitKey })
    expect(submitted.status).toBe(202)
    const job = (submitted.body as { job: Job; created: boolean }).job
    expect(job.status).toBe('queued')
    const again = call('POST', `/meal-requests/${first.id}/submit`, { version: 2 }, { 'idempotency-key': submitKey })
    expect((again.body as { job: Job; created: boolean })).toMatchObject({ created: false, job: { id: job.id } })
    // 生成中不能修改
    expect(errorCode(call('PUT', `/meal-requests/${first.id}`, { version: 2, draft: { ...draft, dish_count: 4 } })).details.reason)
      .toBe('GENERATION_IN_PROGRESS')

    tick(3)
    const running = jobsOf(first.id)[0]
    expect(running).toMatchObject({ status: 'running', attempts: 1, waited_seconds: 3 })
    const stageA = running.stage
    tick(4)
    expect(jobsOf(first.id)[0].stage).not.toBe(stageA) // 階段會變
    expect(get<MealRequest>(`/meal-requests/${first.id}`).status).toBe('running')
    tick(2)
    const done = jobsOf(first.id)[0]
    expect(done.status).toBe('succeeded')
    expect(done.result_summary).toMatchObject({ result_version: 1, mode: 'database_menu' })

    const result = latestResult(first.id)
    expect(result.dishes).toHaveLength(3)
    expect(result.staple?.label).toBe('白飯')
    expect(result.eligibility.adoptable).toBe(true)
    expect(result.nutrition.within_range).toBe(true)

    const adoptKey = key()
    const body = { meal_request_id: first.id, result_version: 1, request_version: 2 }
    const adopted = call('POST', '/meal-plans', body, { 'idempotency-key': adoptKey })
    expect(adopted.status, JSON.stringify(adopted.body)).toBe(201)
    const plan = (adopted.body as { meal_plan: MealPlanSummary }).meal_plan
    expect(call('POST', '/meal-plans', body, { 'idempotency-key': adoptKey }).status).toBe(200) // 重送同一把鍵：同一筆

    const onDate = get<{ meal_plans: MealPlanSummary[] }>('/meal-plans', { date }).meal_plans
    expect(onDate.map((p) => p.id)).toContain(plan.id)
    expect(get<{ dates: { meal_date: string }[] }>('/meal-plans').dates.map((d) => d.meal_date)).toContain(date)
    expect(get<MealRequest>(`/meal-requests/${first.id}`).status).toBe('confirmed')
    // 已採用的問卷是唯讀的
    expect(errorCode(call('PUT', `/meal-requests/${first.id}`, { version: 2, draft: { ...draft, dish_count: 4 } })).details.reason)
      .toBe('ALREADY_ADOPTED')
    expect(call('POST', `/meal-requests/${first.id}/submit`, { version: 2 }, { 'idempotency-key': key() }).status).toBe(409)
  })

  it('fail：工作失敗並附可讀的原因；取消：排隊或執行中都能取消', () => {
    const failing = submitNew('不要太鹹 fail')
    tick(10)
    const failedJob = jobsOf(failing.request.id)[0]
    expect(failedJob.status).toBe('failed')
    expect(failedJob.error?.message).toContain('生成失敗')
    expect(get<MealRequest>(`/meal-requests/${failing.request.id}`).status).toBe('failed')

    const slow = submitNew('slow')
    tick(8)
    expect(jobsOf(slow.request.id)[0].status).toBe('running')
    const cancelled = call('POST', `/meal-requests/${slow.request.id}/cancel`)
    expect(cancelled.status).toBe(200)
    expect((cancelled.body as { job: Job; meal_request: MealRequest })).toMatchObject({
      job: { status: 'failed', stage: 'cancelled', error: { code: 'CANCELLED' } }, meal_request: { status: 'draft' },
    })
    expect(call('POST', `/meal-requests/${slow.request.id}/cancel`).status).toBe(409) // 沒有進行中的生成
    tick(3600)
    expect(jobsOf(slow.request.id)[0].status).toBe('failed') // 取消後不會又自己完成
  })

  it('問卷修改後，舊結果不能採用', () => {
    const { request } = submitNew()
    tick(10)
    const draft = { ...request.draft, dish_count: 2 }
    expect((call('PUT', `/meal-requests/${request.id}`, { version: request.version, draft }).body as MealRequest).status).toBe('draft')
    const result = latestResult(request.id)
    expect(result.eligibility.blocks.map((b) => b.code)).toContain('VERSION_CONFLICT')
    const res = call('POST', '/meal-plans', { meal_request_id: request.id, result_version: 1, request_version: request.version + 1 },
      { 'idempotency-key': key() })
    expect(res.status).toBe(409)
  })
})

describe('種子結果：每一種畫面狀態都看得到', () => {
  beforeEach(() => login())

  it('問卷清單有草稿、生成中、等你決定、有結果、已採用、失敗與選菜模式', () => {
    const rows = requests()
    for (const status of ['draft', 'queued', 'awaiting_decision', 'result_ready', 'confirmed', 'failed']) {
      expect(rows.map((r) => r.status), status).toContain(status)
    }
    expect(rows.filter((r) => r.mode === 'picker').length).toBeGreaterThanOrEqual(2)
    // 生成中的那份：5 秒後開始執行，而且會執行很久
    tick(6)
    expect(seeded('running')).toBeTruthy()
    tick(300)
    expect(seeded('running')).toBeTruthy()
  })

  it('有結果的那份：四道菜、一人超出熱量上限、未知不是 0、缺價不擋採用、知識附出處', () => {
    const result = latestResult(seeded('result_ready').id)
    expect(result.dishes.map((d) => d.dish_name)).toEqual(['地瓜糙米飯', '清蒸鱸魚', '蒜炒高麗菜', '味噌豆腐湯'])
    expect(result.staple).toBeNull()

    // 價格：鱸魚查不到 → 總額未知（null），已知部分另外給；整包花費與按比例成本是兩個數字
    const unknown = result.prices.items.filter((i) => i.price_status === 'unknown')
    expect(unknown.map((i) => i.name)).toEqual(['鱸魚片'])
    expect(unknown[0]).toMatchObject({ cost: null, purchase_price: null, packs: null })
    expect(result.prices).toMatchObject({ budget_status: 'PRICE_INCOMPLETE', price_complete: false, purchase_total: null, cost_total: null })
    expect(Number(result.prices.known_purchase_total)).toBeGreaterThan(Number(result.prices.known_cost_total))
    // 家中已有的蒜頭不列入價格
    expect(result.prices.items.map((i) => i.name)).not.toContain('蒜頭')
    expect(result.eligibility).toMatchObject({ adoptable: true, price_exception_accepted: true, pending_decisions: [] })

    // 營養：整餐通過；膳食纖維與糖因為缺資料是未知（附已知部分與缺值食材）
    const n = result.nutrition
    expect(n.within_range).toBe(true)
    expect(Number(n.energy_kcal)).toBeLessThanOrEqual(Number(n.target!.energy_kcal) * 1.1)
    expect(Number(n.protein_g)).toBeGreaterThanOrEqual(Number(n.target!.protein_g) * 0.9)
    expect(n.other_nutrients.dietary_fiber_g).toBeNull()
    expect(n.missing_by_nutrient?.dietary_fiber_g).toEqual(['海帶芽'])
    expect(Number(n.known_partial?.dietary_fiber_g)).toBeGreaterThan(0)
    expect(n.other_nutrients.sugars_g).toBeNull()

    // 每人：剛好一位（食量設得比需求大的小安）超過自己這一餐熱量上限的 110%
    const people = n.per_person!
    expect(people).toHaveLength(3)
    const over = people.filter((p) => Number(p.intake.energy_kcal) > Number(p.daily.energy_kcal.value) * Number(p.meal_ratio) * 1.1)
    expect(over.map((p) => p.name)).toEqual(['小安'])
    for (const p of people) {
      expect(p.intake.dietary_fiber_g).toBeNull()
      expect(p.known_partial.dietary_fiber_g).toBeDefined()
    }
    expect(people.reduce((s, p) => s + Number(p.share), 0)).toBeCloseTo(1, 3)
    // 味噌湯每人份的鈉超過門檻：只提醒，不影響採用
    expect(result.dishes.find((d) => d.dish_name === '味噌豆腐湯')).toMatchObject({ high_sodium: true })
    expect(result.dishes.find((d) => d.dish_name === '蒜炒高麗菜')).toMatchObject({ high_sodium: false })

    // 知識：每一則都有出處文字與網址
    expect(result.knowledge.status).toBe('available')
    expect(result.knowledge.items.length).toBeGreaterThan(0)
    for (const item of result.knowledge.items) {
      expect(item.citations[0].attribution).toContain('資料來源')
      expect(item.citations[0].source_url).toMatch(/^https:\/\/example\.com\//)
    }
    // 今天午餐已採用的菜列在「避免重複」裡
    expect(result.previous_day.excluded_dishes.some((d) => d.from === 'lunch')).toBe(true)
    expect(result.previous_day_check?.status).toBe('ok')
  })

  it('等你決定的那份：熱量未知 → 可以「繼續查看」，但仍不能採用', () => {
    const row = seeded('awaiting_decision')
    const result = latestResult(row.id)
    expect(result.nutrition.energy_kcal).toBeNull()
    expect(Number(result.nutrition.known_partial?.energy_kcal)).toBeGreaterThan(0)
    expect(result.eligibility.pending_decisions.map((d) => d.kind)).toEqual(['continue_missing_nutrition'])
    // 只帶便當的份另外列，不併入這一餐
    expect(result.nutrition.per_person!.some((p) => p.bento !== null)).toBe(true)
    for (const p of result.nutrition.per_person!) expect(p.intake.energy_kcal).toBeNull()

    const body = { kind: 'continue_missing_nutrition', request_version: result.request_version }
    const decisionKey = key()
    const decided = call('POST', `/meal-requests/${row.id}/results/${result.result_version}/decisions`, body, { 'idempotency-key': decisionKey })
    expect(decided.status).toBe(201)
    expect(call('POST', `/meal-requests/${row.id}/results/${result.result_version}/decisions`, body, { 'idempotency-key': decisionKey }).status).toBe(200)
    expect(get<MealRequest>(`/meal-requests/${row.id}`).status).toBe('result_ready')
    const after = latestResult(row.id)
    expect(after.eligibility).toMatchObject({ adoptable: false, pending_decisions: [] })
    expect(after.eligibility.decisions).toHaveLength(1)
    // 缺價例外現在不需要決定：送了也不適用
    const price = call('POST', `/meal-requests/${row.id}/results/${result.result_version}/decisions`,
      { kind: 'accept_missing_price', request_version: result.request_version }, { 'idempotency-key': key() })
    expect(errorCode(price).details.reason).toBe('DECISION_NOT_APPLICABLE')
    const adopt = call('POST', '/meal-plans', { meal_request_id: row.id, result_version: result.result_version, request_version: result.request_version },
      { 'idempotency-key': key() })
    expect(adopt.status).toBe(422)
    expect(errorCode(adopt).code).toBe('NUTRITION_INCOMPLETE')
  })

  it('已採用的那份：今天午餐在已採用紀錄裡，快照有完整的菜、營養與價格', () => {
    const row = seeded('confirmed')
    const todayIso = localDate(clock)
    const plans = get<{ meal_plans: (MealPlanSummary & { nutrition?: MealResult['nutrition']; prices?: MealResult['prices']; snapshot_available: boolean })[] }>(
      '/meal-plans', { date: todayIso }).meal_plans
    const plan = plans.find((p) => p.meal_request_id === row.id)!
    expect(plan).toMatchObject({ meal_type: 'lunch', status: 'adopted', result_version: 1, snapshot_available: true })
    expect(plan.dishes.map((d) => d.dish_name)).toEqual(['香煎鮭魚', '清炒青江菜', '番茄蛋花湯'])
    expect(plan.nutrition?.within_range).toBe(true)
    expect(plan.prices?.status).toBe('computed')
    expect(latestResult(row.id).request_status).toBe('confirmed')
  })
})

describe('知識與價格重試', () => {
  beforeEach(() => login())

  it('noknow：知識不可用、可只重試知識 → 幾秒後出現新版本', () => {
    const { request } = submitNew('noknow')
    tick(10)
    const first = latestResult(request.id)
    expect(first.knowledge).toMatchObject({ status: 'unavailable', retry_available: true, items: [] })
    expect(first.eligibility.adoptable).toBe(true) // 知識不可用不影響採用
    const retry = call('POST', `/meal-requests/${request.id}/results/1/knowledge-retry`, { request_version: first.request_version },
      { 'idempotency-key': key() })
    expect(retry.status).toBe(202)
    tick(2)
    expect(latestResult(request.id).retries?.knowledge?.status).toBe('running')
    expect(jobsOf(request.id, 'knowledge_retry')).toHaveLength(1)
    tick(3)
    expect(get<MealResult>(`/meal-requests/${request.id}/results/1`).retries?.knowledge)
      .toMatchObject({ status: 'succeeded', produced_result_version: 2 })
    const second = latestResult(request.id)
    expect(second).toMatchObject({ result_version: 2, derived_from_result_version: 1, knowledge: { status: 'available', retry_available: false } })
    expect(second.knowledge_retry?.from_result_version).toBe(1)
    expect(second.dishes.map((d) => d.dish_name)).toEqual(first.dishes.map((d) => d.dish_name)) // 菜不變
  })

  it('nf：查無知識，不能重試', () => {
    const { request } = submitNew('nf')
    tick(10)
    const result = latestResult(request.id)
    expect(result.knowledge).toMatchObject({ status: 'not_found', retry_available: false })
    const retry = call('POST', `/meal-requests/${request.id}/results/1/knowledge-retry`, { request_version: result.request_version },
      { 'idempotency-key': key() })
    expect(errorCode(retry).details.reason).toBe('RETRY_NOT_APPLICABLE')
  })

  it('缺價重試：幾秒後的新版本補上價格', () => {
    const row = seeded('result_ready')
    const first = latestResult(row.id)
    const retry = call('POST', `/meal-requests/${row.id}/results/1/price-retry`, { request_version: first.request_version }, { 'idempotency-key': key() })
    expect(retry.status).toBe(202)
    tick(5)
    const second = latestResult(row.id)
    expect(second.result_version).toBe(2)
    expect(second.prices).toMatchObject({ budget_status: 'OK', price_complete: true })
    expect(second.prices.cost_total).not.toBeNull()
    expect(second.price_retry?.from_result_version).toBe(1)
    expect(second.nutrition.energy_kcal).toBe(first.nutrition.energy_kcal) // 營養不變
  })

  it('overbudget：已知成本超過預算，不能採用', () => {
    const { request } = submitNew('overbudget')
    tick(10)
    const result = latestResult(request.id)
    expect(result.prices.budget_status).toBe('BUDGET_EXCEEDED')
    expect(result.eligibility.blocks.map((b) => b.code)).toEqual(['BUDGET_EXCEEDED'])
  })

  it('指定食譜庫沒有的菜：改走 AI 生成菜單並標示原因', () => {
    const { request } = submitNew('', { specified_dishes: ['阿嬤的滷肉'] })
    tick(10)
    const result = latestResult(request.id)
    expect(result).toMatchObject({ mode: 'ai_generated_menu', fallback_reason: 'SPECIFIED_DISH_NOT_IN_DATABASE' })
    expect(result.fallback_message).toBeTruthy()
    expect(result.dishes.some((d) => d.dish_name === '阿嬤的滷肉' && d.source === 'ai')).toBe(true)
  })
})

describe('食譜庫選菜與資料庫菜單', () => {
  beforeEach(() => login())

  it('食譜庫至少 30 道，有四種分類與衝突標示', () => {
    const picker = requests().find((r) => r.mode === 'picker' && r.status === 'draft')!
    const catalog = get<RecipeCatalog>(`/meal-requests/${picker.id}/recipe-catalog`)
    expect(catalog.dishes.length).toBeGreaterThanOrEqual(30)
    expect(catalog.facets.category.map((f) => f.value)).toEqual(['主菜', '湯', '主食', '點心'])
    expect(catalog.facets.cuisine.length).toBeGreaterThanOrEqual(4)
    expect(catalog.facets.main_ingredient.map((f) => f.value)).toEqual(expect.arrayContaining(['海鮮', '豬肉', '雞肉', '牛肉', '蛋', '豆腐', '蔬菜（無肉）']))
    expect(catalog.facets.staple_kind.map((f) => f.value)).toEqual(expect.arrayContaining(['飯', '麵', '粥']))
    // 這份問卷有小惠（花生過敏）與小安（白蝦過敏、不吃青椒）
    expect(catalog.dishes.find((d) => d.dish_name === '宮保雞丁')!.conflicts).toEqual(['花生'])
    expect(catalog.dishes.find((d) => d.dish_name === '蒜蓉蒸蝦')!.conflicts).toEqual(['白蝦'])
    expect(catalog.dishes.find((d) => d.dish_name === '蒜炒高麗菜')!.conflicts).toEqual([])
  })

  it('選菜模式：菜單 → 營養預覽 → 小知識 → 採用 → 一鍵購入', () => {
    const picker = requests().find((r) => r.mode === 'picker' && r.status === 'draft')!
    const row = get<MealRequest>(`/meal-requests/${picker.id}`)
    expect(row.complete).toBe(true) // 選菜模式不需要預算
    const menu = get<RecipeRetrieval>(`/meal-requests/${picker.id}/recipes`)
    expect(menu.selected.map((r) => r.recipe_name)).toEqual(['蔥油雞腿', '蒜香菠菜', '玉米蘿蔔湯'])
    const recipeIds = menu.selected.map((r) => r.recipe_id)
    const preview = call('POST', `/meal-requests/${picker.id}/nutrition-preview`, { recipe_ids: recipeIds }).body as NutritionPreview
    expect(preview.status).toBe('nutrition_passed')
    expect(preview.people).toHaveLength(2)
    expect(preview.staple?.name).toBe('白飯')
    const tips = call('POST', `/meal-requests/${picker.id}/knowledge-tips`, { recipe_ids: recipeIds }).body as KnowledgeTips
    expect(tips.status).toBe('available')
    for (const tip of tips.items) expect(tip.citations[0].source_url).toMatch(/^https:\/\/example\.com\//)

    const adopted = call('POST', '/meal-plans', { meal_request_id: picker.id, request_version: row.version, recipe_ids: recipeIds },
      { 'idempotency-key': key() })
    expect(adopted.status, JSON.stringify(adopted.body)).toBe(201)
    const plan = (adopted.body as { meal_plan: MealPlanSummary }).meal_plan
    expect(plan).toMatchObject({ result_version: null, market_shortages_available: true })
    expect(Number(plan.energy_kcal)).toBeGreaterThan(0)
    // 採用後再查菜單：回採用當下那一份
    expect(get<RecipeRetrieval>(`/meal-requests/${picker.id}/recipes`).adopted_plan?.id).toBe(plan.id)
    const handoff = call('POST', '/sim-market/handoffs', { plan_id: plan.id })
    expect((handoff.body as { checkout_url: string }).checkout_url).toContain('http://localhost:5174/__fake-market/checkout#handoff=')
  })

  it('不採用，換一份：同一版問卷改給下一組，換完就沒有了', () => {
    login('外婆家')
    const row = requests().find((r) => r.status === 'draft')!
    const first = get<RecipeRetrieval>(`/meal-requests/${row.id}/recipes`)
    expect(first.status).toBe('database_menu')
    const ids = (menu: RecipeRetrieval) => menu.selected.map((r) => r.recipe_id)
    expect(call('POST', `/meal-requests/${row.id}/menu-rejections`, { version: row.version, recipe_ids: ids(first) }).status).toBe(204)
    const second = get<RecipeRetrieval>(`/meal-requests/${row.id}/recipes`)
    expect(second.rejected_count).toBe(1)
    expect(ids(second)).not.toEqual(ids(first))
    let menu = second
    for (let i = 0; i < 6 && menu.selected.length; i += 1) {
      call('POST', `/meal-requests/${row.id}/menu-rejections`, { version: row.version, recipe_ids: ids(menu) })
      menu = get<RecipeRetrieval>(`/meal-requests/${row.id}/recipes`)
    }
    expect(menu).toMatchObject({ status: 'all_menus_rejected', reason: 'ALL_MENUS_REJECTED', selected: [] })
  })

  it('指定菜有成員過敏的食材、或和主食重複：回報原因，不默默換掉', () => {
    const members = get<{ members: Member[] }>('/members').members
    const base = { mode: 'picker', meal_date: addDays(localDate(clock), 6), meal_type: 'dinner', staple: '白飯', dish_count: 1,
      participants: [{ member_id: members[2].id, dining: true, bento: false, bento_meal: 'lunch' }] }
    const shrimp = call('POST', '/meal-requests', { ...base, specified_dishes: ['蒜蓉蒸蝦'] }).body as MealRequest
    expect(get<RecipeRetrieval>(`/meal-requests/${shrimp.id}/recipes`)).toMatchObject({ reason: 'SPECIFIED_DISH_DIETARY_CONFLICT', selected: [] })
    const rice = call('POST', '/meal-requests', { ...base, specified_dishes: ['番茄蛋炒飯'] }).body as MealRequest
    expect(get<RecipeRetrieval>(`/meal-requests/${rice.id}/recipes`).reason).toBe('SPECIFIED_DISH_STAPLE_CONFLICT')
    const missing = call('POST', '/meal-requests', { ...base, specified_dishes: ['不存在的菜'] }).body as MealRequest
    expect(get<RecipeRetrieval>(`/meal-requests/${missing.id}/recipes`)).toMatchObject({ reason: 'SPECIFIED_DISH_NOT_IN_DATABASE', fallback_eligible: true })
    expect(call('POST', '/meal-requests', { ...base, specified_dishes: ['隨便'] }).status).toBe(422)
  })
})

describe('已採用紀錄與趨勢', () => {
  beforeEach(() => login())

  it('這個月有多筆紀錄；7 天趨勢有一天營養未知（null，不是 0），也有一天沒有紀錄', () => {
    const todayIso = localDate(clock)
    const dates = get<{ dates: { meal_date: string; count: number }[] }>('/meal-plans').dates
    expect(dates.length).toBeGreaterThanOrEqual(15)
    expect(dates[0].meal_date).toBe(todayIso)
    expect(dates.find((d) => d.meal_date === addDays(todayIso, -2))?.count).toBe(2)

    type Item = { meal_date: string; energy_kcal: string | number | null; cost_total?: string | null; known_cost_total?: string | null }
    const trend = get<{ since: string; until: string; items: Item[] }>('/meal-plans/trend', { days: '7', until: todayIso })
    expect(trend).toMatchObject({ since: addDays(todayIso, -6), until: todayIso })
    const unknown = trend.items.filter((i) => i.energy_kcal === null)
    expect(unknown.map((i) => i.meal_date)).toEqual([addDays(todayIso, -4)])
    expect(trend.items.every((i) => i.energy_kcal === null || Number(i.energy_kcal) > 0)).toBe(true)
    expect(trend.items.some((i) => i.meal_date === addDays(todayIso, -5))).toBe(false)
    // 價格不完整的餐：成本總額未知，只給已知部分
    for (const item of trend.items.filter((i) => i.cost_total === null && i.known_cost_total !== undefined)) {
      expect(Number(item.known_cost_total)).toBeGreaterThan(0)
    }
    expect(get<{ items: Item[] }>('/meal-plans/trend', { days: '30', until: todayIso }).items.length).toBeGreaterThan(trend.items.length)
    expect(call('GET', '/meal-plans/trend', {}, {}, { query: { days: '0' } }).status).toBe(422)
  })

  it('三種紀錄：生成結果的完整快照、資料庫菜單的快照、只有菜名的最小紀錄', () => {
    const todayIso = localDate(clock)
    type Detail = MealPlanSummary & { snapshot_available: boolean; snapshot: { selected?: unknown[]; people?: unknown[] }; nutrition?: unknown }
    const twoDaysAgo = get<{ meal_plans: Detail[] }>('/meal-plans', { date: addDays(todayIso, -2) }).meal_plans
    expect(twoDaysAgo.map((p) => p.meal_type)).toEqual(['lunch', 'dinner'])
    expect(twoDaysAgo[0].result_version).toBeNull()
    expect(twoDaysAgo[0].snapshot.selected).toHaveLength(3)
    expect(twoDaysAgo[0].snapshot.people).toHaveLength(2)
    expect(Number(twoDaysAgo[0].energy_kcal)).toBeGreaterThan(0)
    expect(twoDaysAgo[1]).toMatchObject({ result_version: 1, snapshot_available: true, energy_kcal: null })
    expect(twoDaysAgo[1].nutrition).toBeDefined()
    const legacy = get<{ meal_plans: Detail[] }>('/meal-plans', { date: addDays(todayIso, -4) }).meal_plans[0]
    expect(legacy).toMatchObject({ snapshot_available: false, energy_kcal: null, protein_g: null })
    expect(call('POST', '/sim-market/handoffs', { plan_id: legacy.id }).status).toBe(409)
    expect(get<{ meal_plans: Detail[] }>('/meal-plans', { date: addDays(todayIso, -5) }).meal_plans).toEqual([])
  })

  it('昨天吃過的菜，今天生成時會避開', () => {
    const todayIso = localDate(clock)
    const yesterday = get<{ meal_plans: MealPlanSummary[] }>('/meal-plans', { date: addDays(todayIso, -1) }).meal_plans
    const eaten = yesterday.flatMap((p) => p.dishes.map((d) => d.dish_name))
    const { request } = submitNew('', { meal_date: todayIso, meal_type: 'breakfast' })
    tick(10)
    const result = latestResult(request.id)
    expect(result.previous_day.has_records).toBe(true)
    for (const dish of result.dishes) expect(eaten).not.toContain(dish.dish_name)
  })
})

describe('其他端點', () => {
  beforeEach(() => login())

  it('食材搜尋、主食選項、服務狀態', () => {
    const found = get<{ ingredients: { name: string }[] }>('/ingredients', { q: '豆腐', limit: '6' }).ingredients
    expect(found.map((i) => i.name)).toEqual(expect.arrayContaining(['板豆腐', '嫩豆腐']))
    expect(get<{ ingredients: unknown[] }>('/ingredients', { q: '蕃茄' }).ingredients).toHaveLength(1) // 別名也找得到
    expect(get<{ none_value: string; options: { label: string }[] }>('/meal-requests/staple-options'))
      .toMatchObject({ none_value: '不需要主食', options: [{ label: '白飯' }, { label: '糙米飯' }, { label: '不指定' }] })
    expect(get<Record<string, { status: string }>>('/health/team')).toMatchObject({
      recognition: { status: 'online' }, knowledge: { status: 'online' }, assistant_openai: { status: 'not_configured' } })
    expect(get<{ status: string }>('/health').status).toBe('ok')
  })

  it('辨識：四種蔬菜，一種對不到標準食材；可確認、重試、刪除', () => {
    const row = seeded('draft')
    const created = call('POST', '/recognitions', { meal_request_id: row.id }, {}, { filename: 'fridge.jpg' })
    expect(created.status).toBe(201)
    const result = created.body as { id: string; suggestions: { name: string; ingredient_id: string | null }[]; image_url: string }
    expect(result.suggestions).toHaveLength(4)
    expect(result.suggestions.filter((s) => s.ingredient_id === null)).toHaveLength(1)
    expect(call('GET', result.image_url).raw?.contentType).toBe('image/svg+xml')
    const first = result.suggestions[0]
    const confirmed = call('POST', `/recognitions/${result.id}/confirm`, { name: first.name, ingredient_id: first.ingredient_id })
    expect((confirmed.body as { confirmed: unknown[] }).confirmed).toHaveLength(1)
    expect(call('POST', `/recognitions/${result.id}/confirm`, { name: 'x', ingredient_id: 'NOPE' }).status).toBe(422)
    expect(call('POST', `/recognitions/${result.id}/retry`).status).toBe(409)
    expect(call('DELETE', `/recognitions/${result.id}`).status).toBe(204)

    const offline = call('POST', '/recognitions', {}, {}, { filename: 'offline.jpg' }).body as { id: string; status: string; retryable: boolean }
    expect(offline).toMatchObject({ status: 'unavailable', retryable: true })
    expect((call('POST', `/recognitions/${offline.id}/retry`).body as { status: string }).status).toBe('succeeded')
    expect((call('POST', '/recognitions', {}, {}, { filename: 'empty.png' }).body as { status: string; suggestions: unknown[] }))
      .toMatchObject({ status: 'no_result', suggestions: [] })
  })

  it('飲食小幫手：串流順序、參考資料、不回答醫療問題', () => {
    const answer = call('POST', '/assistant/chat', { message: '蛋白質要怎麼吃？', history: [], engine: 'local' })
    expect(answer.sse!.map((e) => e.event)).toEqual(['meta', 'references', 'delta', 'delta', 'delta', 'done'])
    const references = answer.sse![1].data as { n: number; url: string; source_name: string }[]
    expect(references.map((r) => r.n)).toEqual([1, 2])
    expect(answer.sse!.at(-1)!.data).toMatchObject({ type: 'answer', citations: [1, 2] })
    const text = answer.sse!.filter((e) => e.event === 'delta').map((e) => (e.data as { text: string }).text).join('')
    expect(text).toContain('[1]')

    const medical = call('POST', '/assistant/chat', { message: '糖尿病要吃什麼藥，劑量多少？' })
    expect(medical.sse![0].data).toMatchObject({ type: 'declined_medical' })
    expect(medical.sse!.some((e) => e.event === 'references')).toBe(false)
    expect(call('POST', '/assistant/chat', { message: '山藥有什麼營養' }).sse![0].data).toMatchObject({ type: 'category_answer', fallback: { food: '山藥' } })
    expect(call('POST', '/assistant/chat', { message: '今天天氣如何' }).sse![0].data).toMatchObject({ type: 'out_of_scope' })
    expect(call('POST', '/assistant/chat', { message: 'offline' }).status).toBe(503)
    expect(call('POST', '/assistant/chat', { message: '' }).status).toBe(422)

    const recipes = call('POST', '/assistant/recipes', { answer: text, type: 'answer' }).body as { recipes: { dish_name: string; foods: string[] }[]; household_filtered: boolean }
    expect(recipes.household_filtered).toBe(true)
    expect(recipes.recipes.length).toBeGreaterThan(0)
    expect(recipes.recipes.length).toBeLessThanOrEqual(3)
    expect(call('POST', '/assistant/recipes', { answer: text, type: 'declined_medical' }).body).toEqual({ recipes: [], household_filtered: false })
  })

  it('營養來源回饋：版本不同回 409，四種狀態都有機會出現', () => {
    const row = seeded('result_ready')
    const menu = latestResult(row.id)
    const recipeIds = menu.dishes.map((d) => d.recipe_id)
    expect(call('POST', `/meal-requests/${row.id}/t04-data`, { request_version: 99, recipe_ids: recipeIds }).status).toBe(409)
    const feedback = call('POST', `/meal-requests/${row.id}/t04-data`, { request_version: row.version, recipe_ids: recipeIds })
      .body as { input_version: number; ingredients: { name: string; status: string; sources: unknown[] }[]; recipes: unknown[] }
    expect(feedback.recipes).toHaveLength(4)
    expect(feedback.ingredients.find((i) => i.name === '鱸魚片')?.status).toBe('source_candidate_needs_confirmation')
    expect(feedback.ingredients.find((i) => i.name === '海帶芽')?.status).toBe('source_values_incomplete')
    expect(feedback.ingredients.find((i) => i.name === '高麗菜')?.status).toBe('source_available_pending_review')
  })

  it('控制端點：重設回種子資料', () => {
    const before = requests().length
    submitNew()
    expect(requests().length).toBe(before + 1)
    expect(call('POST', '/__fake/reset').status).toBe(204)
    expect(call('GET', '/auth/me').status).toBe(401)
    login()
    expect(requests().length).toBe(before)
  })
})

describe('小工具', () => {
  it('暗號只認獨立的英文字', () => {
    expect(flagsOf('想吃清淡 fail')).toEqual(['fail'])
    expect(flagsOf('more info, comfort food')).toEqual([])
    expect(flagsOf('noknowever')).toEqual(['noknowever', 'noknow'])
  })

  it('不是菜名的回答會被擋下', () => {
    expect(notADishName('番茄炒蛋')).toBeNull()
    expect(notADishName('123')).toContain('不是菜名')
    expect(notADishName('都可以啦')).toContain('不是菜名')
  })

  it('multipart：取出文字欄位與檔名', () => {
    const boundary = '----fake'
    const raw = Buffer.from([
      `--${boundary}`, 'Content-Disposition: form-data; name="image"; filename="冰箱.jpg"', 'Content-Type: image/jpeg', '', 'BINARY',
      `--${boundary}`, 'Content-Disposition: form-data; name="meal_request_id"', '', 'abc-123', `--${boundary}--`, '',
    ].join('\r\n'), 'utf-8')
    expect(parseMultipart(`multipart/form-data; boundary=${boundary}`, raw)).toEqual({ fields: { meal_request_id: 'abc-123' }, filename: '冰箱.jpg' })
  })
})

describe('HTTP 中介層（不啟動伺服器，直接餵假的 req／res）', () => {
  type Middleware = (req: IncomingMessage, res: ServerResponse, next: () => void) => Promise<void>

  function middleware(): Middleware {
    let captured: Middleware | undefined
    const plugin = fakeApi()
    const configure = plugin.configureServer as unknown as (server: { middlewares: { use: (fn: Middleware) => void } }) => void
    configure({ middlewares: { use: (fn) => { captured = fn } } })
    return captured!
  }

  async function request(run: Middleware, method: string, url: string, body?: unknown, headers: Record<string, string> = {}) {
    const payload = body === undefined ? [] : [Buffer.from(JSON.stringify(body), 'utf-8')]
    const req = Object.assign(Readable.from(payload), {
      method, url, headers: { host: 'localhost:5174', ...(body === undefined ? {} : { 'content-type': 'application/json' }), ...headers },
    }) as unknown as IncomingMessage
    const out = { status: 0, headers: {} as Record<string, string>, text: '', passed: false }
    const res = {
      set statusCode(value: number) { out.status = value },
      setHeader(name: string, value: string) { out.headers[name.toLowerCase()] = value },
      write(chunk: string) { out.text += chunk; return true },
      end(chunk?: string) { out.text += chunk ?? '' },
    } as unknown as ServerResponse
    await run(req, res, () => { out.passed = true })
    return out
  }

  it('JSON、錯誤格式、SSE 串流與非 /api 路徑', async () => {
    const run = middleware()
    expect((await request(run, 'GET', '/src/main.tsx')).passed).toBe(true)
    const unauthenticated = await request(run, 'GET', '/api/auth/me')
    expect(unauthenticated.status).toBe(401)
    expect(JSON.parse(unauthenticated.text)).toEqual({ error: { code: 'UNAUTHENTICATED', message: '請先登入。', details: {} } })

    const loggedIn = await request(run, 'POST', '/api/auth/login', { email: 'demo@example.com', password: 'password123' })
    expect(loggedIn.status).toBe(200)
    expect(loggedIn.headers['content-type']).toContain('application/json')
    const trend = await request(run, 'GET', '/api/ingredients?q=%E8%B1%86%E8%85%90&limit=3')
    expect(JSON.parse(trend.text).ingredients.length).toBeGreaterThan(0)

    const chat = await request(run, 'POST', '/api/assistant/chat', { message: '高麗菜有什麼營養', history: [] })
    expect(chat.headers['content-type']).toContain('text/event-stream')
    expect(chat.text.startsWith('event: meta\ndata: {')).toBe(true)
    expect(chat.text).toContain('event: references\n')
    expect(chat.text.trimEnd().split('\n\n').at(-1)).toContain('event: done')

    expect((await request(run, 'POST', '/api/auth/logout')).status).toBe(204)
    const market = await request(run, 'GET', '/__fake-market/checkout')
    expect(market.headers['content-type']).toContain('text/html')
  })
})
