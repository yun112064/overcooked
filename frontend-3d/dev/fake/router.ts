import type { DecisionKind, Household, Me } from '../../src/api/types.ts'
import { recipeCatalog, searchFoods } from './catalog.ts'
import { ApiFail, asRecord, badFields, isDate, newId, notFound, num, versionConflict } from './core.ts'
import type { FakeRequest, FakeResponse, FakeState, TeamServiceName, UserRow } from './core.ts'
import { assistantChat, assistantRecipes, createRecognition, recognitionRoutes, t04Data } from './extras.ts'
import { databaseMenu, knowledgeTips, nutritionPreview } from './menu.ts'
import { RULE_VERSION, TargetError, dailyTarget } from './nutrition.ts'
import { adopt, marketHandoff, ownedPlan, planDetail, planSummary, planTrend, plansOnDate, recentDates } from './plans.ts'
import {
  householdMembers, memberOptions, memberView, ownedRequest, parseDraft, parseMember, rejectBadReferences,
  requestSummary, requestView, requireComplete, restrictions, stapleOptions, today,
} from './requests.ts'
import {
  advanceJobs, cancelGeneration, decide, findResult, flagsOf, jobView, requestKnowledgeRetry, requestPriceRetry,
  requireIdempotencyKey, resultSummaries, resultView, submitGeneration,
} from './results.ts'
import { DEMO_TAKEN_EMAIL, createState } from './seed.ts'

/**
 * 假後端的路由：handle(state, request) 是純函式（除了改動 state），測試直接呼叫；
 * dev/fakeApi.ts 只是把它接到 Vite 的中介層。路徑、狀態碼與錯誤格式照 backend/app/api。
 */

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
/** 登入用這個密碼一律失敗，用來看錯誤樣式 */
export const WRONG_PASSWORD = 'wrongpass1'

const ok = (body: unknown, status = 200): FakeResponse => ({ status, body })
const noContent = (): FakeResponse => ({ status: 204 })

function currentUser(state: FakeState): UserRow {
  const user = state.users.find((u) => u.id === state.session?.userId)
  if (!user) throw new ApiFail(401, 'UNAUTHENTICATED', '請先登入。')
  return user
}

function me(state: FakeState): Me {
  const user = currentUser(state)
  const current = state.households.find((h) => h.id === state.session?.householdId)
  return { user: { id: user.id, email: user.email }, current_household: current ? { id: current.id, name: current.name } : null }
}

function positiveInt(value: unknown, field: string): number {
  const n = Number(value)
  if (value === undefined || value === null || !Number.isInteger(n) || n < 1) {
    throw badFields([{ field, reason: 'int_parsing', message: `${field} 必須是正整數。` }])
  }
  return n
}

// ---- 登入與家庭 ----

function auth(state: FakeState, action: string, request: FakeRequest): FakeResponse {
  const body = asRecord(request.body)
  const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : ''
  const password = typeof body.password === 'string' ? body.password : ''
  if (action === 'login') {
    if (typeof body.email !== 'string' || typeof body.password !== 'string' || email.length > 254 || password.length > 128) {
      throw badFields([{ field: typeof body.email !== 'string' ? 'email' : 'password', reason: 'missing', message: '請輸入 Email 與密碼。' }])
    }
    if (password === WRONG_PASSWORD) throw new ApiFail(401, 'UNAUTHENTICATED', 'Email 或密碼不正確。')
    let user = state.users.find((u) => u.email === email)
    if (!user) {
      // 沒看過的 Email 當作示範帳號：直接是兩個種子家庭的成員
      user = { id: newId(state, 'user'), email, householdIds: state.households.filter((h) => h.seeded).map((h) => h.id) }
      state.users.push(user)
    }
    // 只有一個家庭時直接選取，省去每次登入都要再選
    state.session = { userId: user.id, householdId: user.householdIds.length === 1 ? user.householdIds[0] : null }
    return ok(me(state))
  }
  if (action === 'register') {
    const problems = []
    if (!EMAIL_PATTERN.test(email) || email.length > 254) problems.push({ field: 'email', reason: 'value_error', message: '請輸入有效的 Email。' })
    if (password.length < 8) problems.push({ field: 'password', reason: 'string_too_short', message: '密碼至少 8 個字元。' })
    if (password.length > 128) problems.push({ field: 'password', reason: 'string_too_long', message: '密碼最多 128 個字元。' })
    if (problems.length) throw badFields(problems)
    if (state.users.some((u) => u.email === email)) {
      throw new ApiFail(409, 'INVALID_INPUT', '此 Email 已註冊，請直接登入。', { field: 'email', reason: 'EMAIL_TAKEN' })
    }
    // 新註冊的帳號沒有任何家庭：玄關會是空的
    const user: UserRow = { id: newId(state, 'user'), email, householdIds: [] }
    state.users.push(user)
    state.session = { userId: user.id, householdId: null }
    return ok(me(state), 201)
  }
  if (action === 'logout') {
    state.session = null
    return noContent()
  }
  throw notFound()
}

function households(state: FakeState, action: string, request: FakeRequest): FakeResponse {
  const user = currentUser(state)
  const out = (h: { id: string; name: string }): Household => ({ id: h.id, name: h.name })
  if (request.method === 'GET' && action === '') {
    const rows = state.households.filter((h) => user.householdIds.includes(h.id)).sort((a, b) => a.createdAt - b.createdAt)
    return ok({ households: rows.map(out), current_household_id: state.session?.householdId ?? null })
  }
  if (request.method === 'POST' && action === '') {
    const raw = asRecord(request.body).name
    const name = typeof raw === 'string' ? raw.trim() : ''
    if (!name) throw badFields([{ field: 'name', reason: 'value_error', message: '請輸入家庭名稱。' }])
    if (name.length > 100) throw badFields([{ field: 'name', reason: 'string_too_long', message: '家庭名稱最多 100 個字。' }])
    const row = { id: newId(state, 'household'), name, createdAt: state.now(), seeded: false }
    state.households.push(row)
    user.householdIds.push(row.id)
    state.session!.householdId = row.id // 建立後直接設為目前家庭
    return ok(out(row), 201)
  }
  if (request.method === 'POST' && action === 'select') {
    const id = String(asRecord(request.body).household_id ?? '')
    const row = state.households.find((h) => h.id === id && user.householdIds.includes(h.id))
    // 不區分「不存在」與「沒有權限」，避免探測其他家庭
    if (!row) throw notFound('找不到此家庭。')
    state.session!.householdId = row.id
    return ok(out(row))
  }
  throw notFound()
}

// ---- 成員 ----

function membersRoutes(state: FakeState, householdId: string, rest: string[], request: FakeRequest): FakeResponse {
  const [id, action] = rest
  const { method } = request
  if (method === 'GET' && id === 'options' && !action) return ok(memberOptions())
  if (!id) {
    if (method === 'GET') {
      const rows = state.members.filter((m) => m.householdId === householdId).sort((a, b) => a.createdAt - b.createdAt)
      return ok({ members: rows.map((m) => memberView(state, m)) })
    }
    if (method === 'POST') {
      const row = { id: newId(state, 'member'), householdId, ...parseMember(state, request.body), version: 1,
        createdAt: state.now(), updatedAt: state.now() }
      state.members.push(row)
      return ok(memberView(state, row), 201)
    }
    throw notFound()
  }
  const member = state.members.find((m) => m.id === id && m.householdId === householdId)
  if (!member) throw notFound('找不到此成員。')
  if (method === 'GET' && !action) return ok(memberView(state, member))
  if (method === 'GET' && action === 'target') {
    try {
      const target = dailyTarget(member, today(state))
      return ok({ member_id: member.id, rule_version: RULE_VERSION,
        daily_reference: { energy_kcal: num(target.energy), protein_g: num(target.protein) }, approved_for_adoption: true })
    } catch (err) {
      if (err instanceof TargetError) throw new ApiFail(422, 'INVALID_INPUT', err.message, { field: err.field, reason: err.reason })
      throw err
    }
  }
  if (method === 'PUT' && !action) {
    const version = positiveInt(asRecord(request.body).version, 'version')
    // 樂觀鎖：版本不同就是別人改過了
    if (member.version !== version) throw versionConflict()
    Object.assign(member, parseMember(state, request.body))
    member.version += 1
    member.updatedAt = state.now()
    return ok(memberView(state, member))
  }
  if (method === 'DELETE' && !action) {
    state.members.splice(state.members.indexOf(member), 1)
    return noContent()
  }
  throw notFound()
}

// ---- 問卷、菜單、結果 ----

function mealRequests(state: FakeState, householdId: string, rest: string[], request: FakeRequest): FakeResponse {
  const [id, action, resultVersion, resultAction] = rest
  const { method } = request
  const members = householdMembers(state, householdId)
  if (!id) {
    if (method === 'GET') {
      const rows = state.requests.filter((r) => r.householdId === householdId).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 50)
      return ok({ meal_requests: rows.map(requestSummary) })
    }
    if (method === 'POST') {
      const draft = parseDraft(request.body)
      rejectBadReferences(members, draft)
      const row = { id: newId(state, 'request'), householdId, status: 'draft', version: 1, draft, createdAt: state.now(), updatedAt: state.now() }
      state.requests.push(row)
      return ok(requestView(state, row), 201)
    }
    throw notFound()
  }
  if (method === 'GET' && id === 'staple-options' && !action) return ok(stapleOptions())
  const row = ownedRequest(state, householdId, id)
  const draft = row.draft

  if (!action) {
    if (method === 'GET') return ok(requestView(state, row))
    if (method === 'PUT') {
      const body = asRecord(request.body)
      const version = positiveInt(body.version, 'version')
      const next = parseDraft(body.draft)
      if (row.status === 'queued' || row.status === 'running') {
        throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷正在生成中，請等待結果後再修改。', { reason: 'GENERATION_IN_PROGRESS' })
      }
      // 內容沒變（例如回看問卷）：不升版、不改狀態，已有的結果與採用都保留
      if (row.version === version && JSON.stringify(next) === JSON.stringify(draft)) return ok(requestView(state, row))
      if (row.status === 'confirmed') {
        throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷已採用，不能再生成或修改；請另建新問卷。', { reason: 'ALREADY_ADOPTED' })
      }
      if (row.version !== version) throw versionConflict()
      rejectBadReferences(members, next)
      row.version += 1
      row.status = 'draft' // 失敗或已有結果後再修改：回到草稿，新版本需重新送出生成
      row.draft = next
      row.updatedAt = state.now()
      return ok(requestView(state, row))
    }
    if (method === 'DELETE') {
      if (row.status === 'queued' || row.status === 'running') {
        throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷正在生成中，不能刪除。', { reason: 'GENERATION_IN_PROGRESS' })
      }
      state.requests.splice(state.requests.indexOf(row), 1)
      return noContent()
    }
    throw notFound()
  }

  if (method === 'POST' && action === 'submit') {
    const key = requireIdempotencyKey(request.headers)
    const version = positiveInt(asRecord(request.body).version, 'version')
    const { job, created } = submitGeneration(state, row, version, key)
    return ok({ job: jobView(state, job), created }, 202)
  }
  if (method === 'POST' && action === 'cancel') {
    const job = cancelGeneration(state, row)
    return ok({ job: jobView(state, job), meal_request: requestView(state, row) })
  }
  if (method === 'GET' && action === 'recipe-catalog') {
    return ok(recipeCatalog(new Set(restrictions(draft, members).excluded_food_ids)))
  }
  if (method === 'GET' && action === 'recipes') {
    const adopted = state.plans.find((p) => p.requestId === row.id)
    if (draft.mode === 'picker' && adopted?.preview && adopted.resultVersion === null) {
      // 選菜模式採用後：直接回採用當下的那份菜單
      return ok({ status: 'database_menu', code: null, reason: null, details: {}, previous_day: null,
        selected: adopted.preview.selected, candidates: [], input_version: row.version, fallback_eligible: false,
        adopted_plan: planSummary(state, adopted) })
    }
    requireComplete(draft, members)
    const menu = databaseMenu(state, row, members)
    return ok({ ...menu, input_version: row.version, fallback_eligible: menu.status === 'no_feasible_database_menu',
      adopted_plan: adopted ? planSummary(state, adopted) : null })
  }
  if (method === 'POST' && action === 'menu-rejections') {
    const body = asRecord(request.body)
    const version = positiveInt(body.version, 'version')
    const recipeIds = Array.isArray(body.recipe_ids) ? body.recipe_ids.map(String) : []
    if (recipeIds.length < 1 || recipeIds.length > 10) throw badFields([{ field: 'recipe_ids', reason: 'too_short', message: '請提供 1 到 10 道菜。' }])
    if (row.version !== version) throw versionConflict()
    if (row.status === 'confirmed') throw new ApiFail(409, 'ALREADY_ADOPTED', '這份問卷已經採用菜單，不能再換。')
    state.rejections.push({ requestId: row.id, version, recipeIds: [...new Set(recipeIds)].sort() })
    return noContent()
  }
  if (method === 'POST' && (action === 'nutrition-preview' || action === 'knowledge-tips')) {
    const recipeIds = asRecord(request.body).recipe_ids
    if (!Array.isArray(recipeIds) || recipeIds.length < 1 || recipeIds.length > 10) {
      throw badFields([{ field: 'recipe_ids', reason: 'too_short', message: '請提供 1 到 10 道菜。' }])
    }
    requireComplete(draft, members)
    const { preview } = nutritionPreview(draft, members, recipeIds.map(String))
    if (action === 'nutrition-preview') return ok(preview)
    return ok({ ...knowledgeTips(preview.final_food_ids, flagsOf(draft.taste_preferences), preview.status),
      input_version: row.version, recipe_ids: recipeIds })
  }
  if (method === 'POST' && action === 't04-data') return ok(t04Data(state, householdId, row.id, request.body))

  if (action === 'results') {
    if (method === 'GET' && !resultVersion) return ok({ results: resultSummaries(state, row) })
    const result = findResult(state, row.id, Number(resultVersion))
    if (method === 'GET' && !resultAction) return ok(resultView(state, row, result))
    if (method === 'POST' && resultAction) {
      const body = asRecord(request.body)
      const key = requireIdempotencyKey(request.headers)
      if (resultAction === 'decisions') {
        if (body.kind !== 'accept_missing_price' && body.kind !== 'continue_missing_nutrition') {
          throw badFields([{ field: 'kind', reason: 'literal_error', message: '決定種類不正確。' }])
        }
        const requestVersion = positiveInt(body.request_version, 'request_version')
        const { decision, created } = decide(state, row, result, body.kind as DecisionKind, requestVersion, key)
        return ok({ decision, result: resultView(state, row, result) }, created ? 201 : 200)
      }
      if (resultAction === 'price-retry' || resultAction === 'knowledge-retry') {
        const requestVersion = positiveInt(body.request_version, 'request_version')
        const { job, created } = resultAction === 'price-retry'
          ? requestPriceRetry(state, row, result, requestVersion, key)
          : requestKnowledgeRetry(state, row, result, requestVersion, key)
        return ok({ job: jobView(state, job), created }, created ? 202 : 200)
      }
    }
  }
  throw notFound()
}

function jobs(state: FakeState, householdId: string, rest: string[], request: FakeRequest): FakeResponse {
  if (request.method !== 'GET') throw notFound()
  const own = state.jobs.filter((j) => j.householdId === householdId)
  if (rest[0]) {
    const job = own.find((j) => j.id === rest[0])
    if (!job) throw notFound('找不到此工作。')
    return ok(jobView(state, job))
  }
  const { meal_request_id: requestId, kind } = request.query
  const rows = own.filter((j) => (!requestId || j.requestId === requestId) && (!kind || j.kind === kind))
    .sort((a, b) => b.createdAt - a.createdAt).slice(0, 50)
  return ok({ jobs: rows.map((j) => jobView(state, j)) })
}

function mealPlans(state: FakeState, householdId: string, rest: string[], request: FakeRequest): FakeResponse {
  const [id] = rest
  if (request.method === 'POST' && !id) return adopt(state, householdId, request.body, request.headers)
  if (request.method !== 'GET') throw notFound()
  if (id === 'trend') return ok(planTrend(state, householdId, request.query))
  if (id) return ok(planDetail(state, ownedPlan(state, householdId, id)))
  const { date } = request.query
  // 指定 date：當日全部餐別的已採用餐食；未指定：最近有紀錄的日期
  if (date === undefined) return ok({ dates: recentDates(state, householdId) })
  if (!isDate(date)) throw badFields([{ field: 'date', reason: 'date_parsing', message: '請輸入有效的日期。' }])
  return ok({ meal_date: date, meal_plans: plansOnDate(state, householdId, date) })
}

// ---- 假後端專用的控制端點（真後端沒有；只給自動化測試重設狀態用，畫面程式不要呼叫） ----

function control(state: FakeState, action: string, request: FakeRequest): FakeResponse {
  if (request.method !== 'POST') throw notFound()
  if (action === 'reset') {
    Object.assign(state, createState({ now: state.now }))
    return noContent()
  }
  if (action === 'team') {
    for (const [name, status] of Object.entries(asRecord(request.body))) {
      if (!(name in state.team) || (status !== 'online' && status !== 'offline' && status !== 'not_configured')) {
        throw badFields([{ field: name, reason: 'literal_error', message: '服務名稱或狀態不正確。' }])
      }
      state.team[name as TeamServiceName] = {
        status, reason: status === 'online' ? null : status === 'offline' ? 'UNREACHABLE' : 'NOT_CONFIGURED',
      }
    }
    return ok(state.team)
  }
  throw notFound()
}

function route(state: FakeState, request: FakeRequest): FakeResponse {
  const [root, ...rest] = request.path.split('/').filter(Boolean)
  const { method } = request

  if (root === 'health' && method === 'GET' && rest.length === 0) return ok({ status: 'ok', service: 'backend' })
  if (root === 'health' && method === 'GET' && rest[0] === 'db') return ok({ status: 'ok', service: 'database' })
  if (root === '__fake') return control(state, rest[0] ?? '', request)
  if (root === 'auth' && method === 'POST') return auth(state, rest[0] ?? '', request)

  currentUser(state) // 以下都要登入
  if (root === 'auth' && method === 'GET' && rest[0] === 'me') return ok(me(state))
  if (root === 'household') return households(state, rest[0] ?? '', request)
  if (root === 'health' && method === 'GET' && rest[0] === 'team') return ok(state.team)
  if (root === 'ingredients' && method === 'GET') {
    const q = request.query.q ?? ''
    const limit = request.query.limit === undefined ? 20 : Number(request.query.limit)
    if (q.length > 50 || !Number.isInteger(limit) || limit < 1 || limit > 50) {
      throw badFields([{ field: q.length > 50 ? 'q' : 'limit', reason: 'value_error', message: '搜尋條件不正確。' }])
    }
    return ok({ ingredients: searchFoods(q, limit) })
  }
  if (root === 'assistant' && method === 'POST') {
    if (rest[0] === 'recipes') return ok(assistantRecipes(state, request.body))
    if (rest[0] === 'chat') {
      if (state.team.assistant.status !== 'online') {
        throw new ApiFail(503, 'ASSISTANT_UNAVAILABLE', '暫時連不到飲食小幫手（團隊電腦可能離線），請稍後再試。', { reason: 'UNREACHABLE' })
      }
      return assistantChat(request)
    }
  }

  const householdId = state.session?.householdId
  if (!householdId) throw new ApiFail(409, 'HOUSEHOLD_NOT_SELECTED', '請先建立或選擇家庭。')
  if (root === 'members') return membersRoutes(state, householdId, rest, request)
  if (root === 'meal-requests') return mealRequests(state, householdId, rest, request)
  if (root === 'jobs') return jobs(state, householdId, rest, request)
  if (root === 'meal-plans') return mealPlans(state, householdId, rest, request)
  if (root === 'recognitions') {
    if (method === 'POST' && rest.length === 0) return ok(createRecognition(state, householdId, request), 201)
    if (rest[0]) return recognitionRoutes(state, householdId, rest[0], rest[1] ?? '', request)
  }
  if (root === 'sim-market' && method === 'POST' && rest[0] === 'handoffs') {
    return ok(marketHandoff(state, householdId, request.body, request.origin))
  }
  throw notFound(`假 API 沒有實作 ${method} ${request.path}`)
}

/** 處理一個請求：先依時鐘推進背景工作，再走路由；ApiFail 轉成 { error: { code, message, details } }。 */
export function handle(state: FakeState, request: FakeRequest): FakeResponse {
  try {
    advanceJobs(state)
    return route(state, request)
  } catch (err) {
    if (err instanceof ApiFail) {
      return { status: err.status, body: { error: { code: err.code, message: err.message, details: err.details } } }
    }
    throw err
  }
}

export { DEMO_TAKEN_EMAIL }
