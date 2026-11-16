import type {
  GenerationPlanDetail, GenerationPlanSummary, MealPlanDetail, MealPlanSummary, MealType,
} from '../../src/api/types.ts'
import { RECIPE_SOURCE } from './catalog.ts'
import { ApiFail, addDays, asRecord, badFields, clone, isDate, iso, newId, notFound, versionConflict } from './core.ts'
import type { FakeState, PlanRow, PreviewSnapshot, RequestRow, ResultRow } from './core.ts'
import { databaseMenu, nutritionPreview, planDate } from './menu.ts'
import { completeness, householdMembers, today } from './requests.ts'
import {
  MODE_LABELS, decisionsFor, eligibility, findResult, previousDayCheck, priceView, requireIdempotencyKey,
} from './results.ts'

/** 採用（兩條路：生成結果、資料庫菜單）與已採用餐食紀錄。已採用的快照是深拷貝，之後不會再變。 */

const RECORD_LABEL = '已採用餐食紀錄'
const MEAL_ORDER: Record<MealType, number> = { breakfast: 0, lunch: 1, dinner: 2 }
const MARKET_TTL_SECONDS = 600

function ordered(state: FakeState, plans: PlanRow[]): PlanRow[] {
  return [...plans].sort((a, b) => planDate(state, a).localeCompare(planDate(state, b))
    || MEAL_ORDER[a.mealType] - MEAL_ORDER[b.mealType] || a.adoptedAt - b.adoptedAt)
}

function householdPlans(state: FakeState, householdId: string): PlanRow[] {
  return state.plans.filter((p) => p.householdId === householdId && p.status === 'adopted')
}

/** 快照：資料庫菜單的欄位照 MealPlanDetail；另外帶版本資訊。 */
type PlanSnapshot = MealPlanDetail['snapshot'] & {
  schema_version?: string
  record_label?: string
  status?: string
  adopted_versions?: { meal_request_id: string | null; request_version: number | null; result_version: number | null }
  market_shortages?: PreviewSnapshot['market_shortages']
}

/** 同一筆紀錄要同時符合兩個頁面的型別（已採用紀錄、營養與採購紀錄），所以用交集型別讓編譯器幫忙檢查。 */
export type PlanSummary = MealPlanSummary & GenerationPlanSummary & { snapshot: PlanSnapshot }
export type PlanDetail = PlanSummary & Partial<GenerationPlanDetail> & Pick<MealPlanDetail, 'snapshot'>

function snapshotOf(plan: PlanRow): PlanSnapshot {
  if (plan.preview) return { schema_version: 'database_menu_snapshot/v2', record_label: RECORD_LABEL, ...plan.preview }
  if (plan.result) {
    return {
      schema_version: 'meal_plan_snapshot/v1', record_label: RECORD_LABEL,
      adopted_versions: { meal_request_id: plan.requestId, request_version: plan.requestVersion, result_version: plan.resultVersion },
    }
  }
  return {} // T11 的最小紀錄：只有菜名，沒有完整快照
}

/** 與後端 meal_plans.summary 相同：資料庫菜單的摘要欄位 + 生成結果的摘要欄位合在一起。 */
export function planSummary(state: FakeState, plan: PlanRow): PlanSummary {
  const totals = plan.preview?.nutrition.totals ?? null
  return {
    id: plan.id,
    meal_date: planDate(state, plan),
    meal_type: plan.mealType,
    mode: plan.mode,
    status: plan.status,
    adopted_at: iso(plan.adoptedAt),
    meal_request_id: plan.requestId,
    // 生成結果的紀錄沒有這兩個摘要值（後端也是 null），完整營養在 nutrition 裡
    energy_kcal: totals?.energy_kcal ?? null,
    protein_g: totals?.protein_g ?? null,
    market_shortages_available: Boolean(plan.preview?.market_shortages),
    snapshot: snapshotOf(plan),
    mode_label: MODE_LABELS[plan.mode],
    ai_generated: plan.mode === 'ai_generated_menu',
    record_label: RECORD_LABEL,
    result_version: plan.resultVersion,
    request_version: plan.requestVersion,
    price_exception_accepted: plan.priceExceptionAccepted,
    dishes: plan.dishes.map((d) => ({ ...d })),
  }
}

export function planDetail(state: FakeState, plan: PlanRow): PlanDetail {
  const summary = planSummary(state, plan)
  if (!plan.result) return { ...summary, snapshot_available: plan.preview !== null }
  const { body, fallbackReason, historyChanged } = plan.result
  const draft = body.requestSnapshot
  return {
    ...summary,
    snapshot_available: true,
    fallback_message: fallbackReason ? '食譜資料庫沒有可行組合，已自動改由 AI 生成。' : null,
    dishes: body.dishes.map((d) => ({
      ...clone(d),
      recipe_source: d.recipe_id ? { recipe_id: d.recipe_id, ...RECIPE_SOURCE } : null,
    })),
    ...{ staple: clone(body.staple), targets: body.targets,
      request: { meal_date: draft.meal_date, meal_type: draft.meal_type, budget: draft.budget, staple: draft.staple,
        dish_count: draft.dish_count, specified_dishes: draft.specified_dishes, taste_preferences: draft.taste_preferences,
        available_ingredient_ids: draft.available_ingredient_ids } },
    nutrition: clone(body.nutrition),
    prices: clone(body.prices),
    knowledge: clone(body.knowledge),
    previous_day: { ...clone(body.previous_day), history_changed: historyChanged },
    missing: {
      price_food_ids: body.prices.items.filter((i) => i.price_status === 'unknown').map((i) => i.food_id).sort(),
      price_exception_accepted: plan.priceExceptionAccepted,
      nutrients: clone(body.nutrition.missing_nutrients),
    },
  }
}

// ---- 讀取 ----

export function plansOnDate(state: FakeState, householdId: string, date: string) {
  return ordered(state, householdPlans(state, householdId).filter((p) => planDate(state, p) === date))
    .map((p) => planDetail(state, p))
}

export function recentDates(state: FakeState, householdId: string, limit = 60): { meal_date: string; count: number }[] {
  const counts = new Map<string, number>()
  for (const plan of householdPlans(state, householdId)) {
    const date = planDate(state, plan)
    counts.set(date, (counts.get(date) ?? 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => b[0].localeCompare(a[0])).slice(0, limit)
    .map(([meal_date, count]) => ({ meal_date, count }))
}

/**
 * 歷史圖表：since～until（含）每筆已採用餐食。與後端相同：生成結果的紀錄回精確字串並附成本，
 * 資料庫菜單與最小紀錄回數字（未知為 null，不是 0）。
 */
export function planTrend(state: FakeState, householdId: string, query: Record<string, string>) {
  const days = query.days === undefined ? 30 : Number(query.days)
  if (!Number.isInteger(days) || days < 1 || days > 366) {
    throw badFields([{ field: 'days', reason: 'int_range', message: 'days 必須是 1 到 366。' }])
  }
  if (query.until !== undefined && !isDate(query.until)) {
    throw badFields([{ field: 'until', reason: 'date_parsing', message: '請輸入有效的日期。' }])
  }
  const until = query.until ?? today(state)
  const since = addDays(until, -(days - 1))
  const inRange = ordered(state, householdPlans(state, householdId)).filter((p) => {
    const date = planDate(state, p)
    return date >= since && date <= until
  })
  const number = (value: string | null | undefined) => (value === null || value === undefined ? null : Number(value))
  const items = inRange.map((plan) => {
    const base = { meal_date: planDate(state, plan), meal_type: plan.mealType }
    if (plan.result) {
      const { nutrition, prices } = plan.result.body
      return {
        ...base, energy_kcal: nutrition.energy_kcal, protein_g: nutrition.protein_g,
        target_energy_kcal: nutrition.target?.energy_kcal ?? null, target_protein_g: nutrition.target?.protein_g ?? null,
        cost_total: prices.cost_total, known_cost_total: prices.known_cost_total, budget: prices.budget,
      }
    }
    const totals = plan.preview?.nutrition.totals
    return {
      ...base, id: plan.id, energy_kcal: number(totals?.energy_kcal), protein_g: number(totals?.protein_g),
      target_energy_kcal: number(plan.preview?.targets.energy_kcal), target_protein_g: number(plan.preview?.targets.protein_g),
    }
  })
  return { since, until, items }
}

export function ownedPlan(state: FakeState, householdId: string, id: string): PlanRow {
  const plan = state.plans.find((p) => p.id === id && p.householdId === householdId)
  if (!plan) throw notFound('找不到此已採用餐食紀錄。')
  return plan
}

// ---- 採用 ----

function newPlan(state: FakeState, fields: Omit<PlanRow, 'id' | 'status'>): PlanRow {
  const plan: PlanRow = { id: newId(state, 'plan'), status: 'adopted', ...fields }
  state.plans.push(plan)
  return plan
}

function confirm(state: FakeState, request: RequestRow): void {
  request.status = 'confirmed'
  request.updatedAt = state.now()
}

/** 採用指定結果版本：重驗版本、資格、缺價接受紀錄與昨日歷史，保存完整快照，問卷改為 confirmed。 */
export function adoptResult(state: FakeState, householdId: string, body: Record<string, unknown>, key: string): { plan: PlanRow; created: boolean } {
  const requestId = String(body.meal_request_id ?? '')
  const resultVersion = Number(body.result_version)
  const requestVersion = Number(body.request_version)
  const decisionId = typeof body.price_exception_decision_id === 'string' ? body.price_exception_decision_id : null
  if (!Number.isInteger(resultVersion) || resultVersion < 1 || !Number.isInteger(requestVersion) || requestVersion < 1) {
    throw badFields([{ field: 'request_version', reason: 'int_parsing', message: '版本必須是正整數。' }])
  }
  const hash = ['adopt', requestId, resultVersion, requestVersion, decisionId ?? ''].join('|')
  const sameKey = state.plans.find((p) => p.householdId === householdId && p.idempotencyKey === key)
  if (sameKey) {
    if (sameKey.commandHash !== hash) throw new ApiFail(409, 'IDEMPOTENCY_CONFLICT', '這個送出識別碼已用於不同的採用內容，請重新操作。')
    return { plan: sameKey, created: false }
  }
  const request = state.requests.find((r) => r.id === requestId && r.householdId === householdId)
  if (!request) throw notFound('找不到此問卷。')
  const adopted = state.plans.find((p) => p.requestId === request.id)
  if (adopted) {
    // 換了送出識別碼的重複點擊：仍是同一筆採用
    if (adopted.resultVersion === resultVersion && adopted.requestVersion === requestVersion) return { plan: adopted, created: false }
    throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷已採用其他結果版本，不能再採用另一版本。',
      { reason: 'ALREADY_ADOPTED', adopted_result_version: adopted.resultVersion })
  }
  if (request.status === 'queued' || request.status === 'running') {
    throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷正在生成中，請等待結果。', { reason: 'GENERATION_IN_PROGRESS' })
  }
  if (requestVersion !== request.version) throw versionConflict()
  const result: ResultRow = findResult(state, request.id, resultVersion)
  const evaluation = eligibility(state, request, result)
  if (!evaluation.adoptable) {
    if (evaluation.blocks.some((b) => b.code === 'VERSION_CONFLICT')) {
      throw new ApiFail(409, 'VERSION_CONFLICT', '問卷已在此結果產生後修改，請重新生成後再採用。', { blocks: evaluation.blocks })
    }
    const first = evaluation.blocks[0]
    throw new ApiFail(422, first.code, `此結果不可採用：${first.message}`, { blocks: evaluation.blocks })
  }
  const accepted = evaluation.price_exception_accepted
    ? decisionsFor(state, result).find((d) => d.kind === 'accept_missing_price') ?? null : null
  if (!accepted && decisionId !== null) {
    throw new ApiFail(422, 'INVALID_INPUT', '此結果沒有可用的缺價例外接受紀錄。', { reason: 'PRICE_EXCEPTION_NOT_APPLICABLE' })
  }
  const check = previousDayCheck(state, householdId, result.body)
  if (check.status === 'conflict') {
    throw new ApiFail(409, 'PREVIOUS_DAY_DISH_CONFLICT', '生成後昨日或同一天其他餐的已採用紀錄有變動，此菜單有重複的菜，請重新生成。',
      { conflicting_dishes: check.conflicting, previous_date: check.previous_date })
  }
  const draft = request.draft
  const plan = newPlan(state, {
    householdId, date: draft.meal_date!, mealType: draft.meal_type!, mode: result.mode, adoptedAt: state.now(),
    requestId: request.id, resultVersion: result.resultVersion, requestVersion: request.version,
    priceExceptionAccepted: evaluation.price_exception_accepted, idempotencyKey: key, commandHash: hash,
    dishes: result.body.dishes.map((d) => ({
      standard_dish_id: d.standard_dish_id, dish_name: d.dish_name, aliases: d.aliases, source: d.source, recipe_id: d.recipe_id,
    })),
    result: { body: clone(result.body), fallbackReason: result.fallbackReason, historyChanged: check.history_changed },
    preview: null,
  })
  confirm(state, request)
  return { plan, created: true }
}

/** 採用資料庫菜單（含食譜庫選菜）：後端重查菜單，必須和畫面上看到的相同、營養通過、成本不超預算。 */
export function adoptPreview(state: FakeState, householdId: string, body: Record<string, unknown>, key: string): { plan: PlanRow; created: boolean } {
  const requestId = String(body.meal_request_id ?? '')
  const version = Number(body.request_version)
  const recipeIds = Array.isArray(body.recipe_ids) ? body.recipe_ids.map(String) : []
  if (!Number.isInteger(version) || version < 1 || recipeIds.length < 1 || recipeIds.length > 10) {
    throw badFields([{ field: 'recipe_ids', reason: 'too_short', message: '請提供 1 到 10 道菜與問卷版本。' }])
  }
  const request = state.requests.find((r) => r.id === requestId && r.householdId === householdId)
  if (!request) throw notFound('找不到此問卷。')
  const members = householdMembers(state, householdId)
  if (completeness(request.draft, members).length) throw new ApiFail(422, 'INVALID_INPUT', '請先完成問卷。')
  const hash = ['adopt-preview', requestId, version, [...new Set(recipeIds)].sort().join(',')].join('|')
  const sameKey = state.plans.find((p) => p.householdId === householdId && p.idempotencyKey === key)
  if (sameKey) {
    if (sameKey.requestId !== request.id || sameKey.commandHash !== hash) {
      throw new ApiFail(409, 'IDEMPOTENCY_KEY_REUSED', '這個請求已用於另一份問卷。')
    }
    return { plan: sameKey, created: false }
  }
  if (state.plans.some((p) => p.requestId === request.id)) throw new ApiFail(409, 'ALREADY_ADOPTED', '這份問卷已經採用過菜單。')
  if (request.version !== version) throw versionConflict()
  if (request.status === 'queued' || request.status === 'running') {
    throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷正在生成中，請等待結果。')
  }
  const draft = request.draft
  const menu = databaseMenu(state, request, members)
  if (menu.selected.map((r) => r.recipe_id).sort().join(',') !== [...new Set(recipeIds)].sort().join(',')) {
    throw new ApiFail(409, 'MENU_CHANGED', '菜單已經變更，請重新查詢後再採用。')
  }
  const { preview, calc } = nutritionPreview(draft, members, menu.selected.map((r) => r.recipe_id))
  if (preview.status !== 'nutrition_passed' || preview.adoption_blocked) {
    throw new ApiFail(409, 'NOT_ADOPTABLE', '這份菜單的營養沒有通過，不能採用。')
  }
  const available = new Set(draft.available_ingredient_ids)
  const prices = priceView(calc.dishes.flatMap((d) => d.lines), available, draft.budget, state.now())
  if (prices.budget_status === 'BUDGET_EXCEEDED') {
    throw new ApiFail(422, 'BUDGET_EXCEEDED', '此結果不可採用：已知食材成本已超過預算，不可採用（缺價例外也不能放寬）。',
      { blocks: [{ code: 'BUDGET_EXCEEDED', known_cost_total: prices.known_cost_total, budget: prices.budget }] })
  }
  const snapshot: PreviewSnapshot = {
    selected: menu.selected,
    staple_choice: draft.staple,
    status: preview.status,
    status_message: preview.status_message,
    targets: preview.targets,
    nutrition: { totals: preview.nutrition.totals },
    nutrition_all: preview.nutrition_all,
    dishes: preview.dishes,
    staple: preview.staple,
    people: preview.people,
    daily_reference_meta: preview.daily_reference_meta,
  }
  if (draft.mode === 'picker') {
    // 食譜庫選菜才保存缺料清單，讓「一鍵購入」有東西可帶去假超市
    snapshot.market_shortages = {
      items: prices.items.map((i) => ({ food_id: i.food_id, name: i.name, shortage_grams: i.shortage_grams })),
      unquantified_items: [],
    }
  }
  const plan = newPlan(state, {
    householdId, date: draft.meal_date!, mealType: draft.meal_type!, mode: 'database_menu', adoptedAt: state.now(),
    requestId: request.id, resultVersion: null, requestVersion: request.version,
    priceExceptionAccepted: prices.budget_status === 'PRICE_INCOMPLETE', idempotencyKey: key, commandHash: hash,
    dishes: menu.selected.map((r) => ({
      standard_dish_id: r.standard_dish_id, dish_name: r.recipe_name, aliases: r.aliases, source: 'database', recipe_id: r.recipe_id,
    })),
    result: null,
    preview: clone(snapshot),
  })
  confirm(state, request)
  return { plan, created: true }
}

export function adopt(state: FakeState, householdId: string, raw: unknown, headers: Record<string, string>) {
  const body = asRecord(raw)
  const key = requireIdempotencyKey(headers)
  // 與後端相同的分派：帶 result_version 是生成結果採用，否則是資料庫菜單採用
  const { plan, created } = body.result_version !== undefined
    ? adoptResult(state, householdId, body, key) : adoptPreview(state, householdId, body, key)
  return { status: created ? 201 : 200, body: { meal_plan: planSummary(state, plan), created } }
}

/** 一鍵購入：回傳假超市的結帳網址（這個假後端自己提供一個示意頁）。 */
export function marketHandoff(state: FakeState, householdId: string, raw: unknown, origin: string | undefined) {
  const planId = String(asRecord(raw).plan_id ?? '')
  const plan = ownedPlan(state, householdId, planId)
  if (!plan.result && !plan.preview?.market_shortages) {
    throw new ApiFail(409, 'MARKET_SNAPSHOT_MISSING', '這份餐單沒有可用的採用缺料快照。')
  }
  return {
    checkout_url: `${origin ?? 'http://localhost:5174'}/__fake-market/checkout#handoff=fake.${plan.id}`,
    expires_at: Math.floor(state.now() / 1000) + MARKET_TTL_SECONDS,
  }
}
