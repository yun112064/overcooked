import type {
  DecisionKind, Job, MealRequestDraft, MealResult, MealResultSummary, NutritionSuggestion, PendingDecision,
  ResultBlock, ResultDecision, ResultIngredient, RetryState,
} from '../../src/api/types.ts'
import { KNOWLEDGE_SOURCE, NUTRIENT_KEYS, food, foodByName, knowledgeFor, knowledgeUrl } from './catalog.ts'
import type { Food } from './catalog.ts'
import { ApiFail, alreadyAdopted, clone, iso, newId, num, numOrNull, versionConflict } from './core.ts'
import type { DecisionRow, FakeState, JobRow, MemberRow, RequestRow, ResultBody, ResultRow } from './core.ts'
import { pickMenu, previousContext } from './menu.ts'
import {
  DAILY_REFERENCE_META, HIGH_SODIUM_DISH_MG, RULE_VERSION, calcMeal, computeTargets, displayAmount, resultPeople,
  stapleFood, withinRange,
} from './nutrition.ts'
import type { DishInput, MealCalc } from './nutrition.ts'
import { checkRequest, householdMembers, stapleLabel } from './requests.ts'

/** 生成工作（依經過的時間推進）、生成結果、採用資格與缺值決策。 */

export const MODE_LABELS = { database_menu: '資料庫菜單', ai_generated_menu: 'AI 生成菜單' } as const
const FALLBACK_MESSAGES: Record<string, string> = {
  NO_MATCHING_RECIPES: '食譜資料庫沒有符合條件的組合，已自動改由 AI 生成。',
  SPECIFIED_DISH_NOT_IN_DATABASE: '指定的菜不在食譜資料庫，已自動改由 AI 生成。',
}
const BLOCK_MESSAGES: Record<string, string> = {
  NUTRITION_INCOMPLETE: '缺少熱量或蛋白質的必要營養資料，可查看但不可採用；補齊並重新驗證後才可採用。',
  NUTRITION_OUT_OF_RANGE: '熱量高於目標 10% 或蛋白質低於目標 10%，可查看差距但不可採用。',
  BUDGET_EXCEEDED: '已知食材成本已超過預算，不可採用（缺價例外也不能放寬）。',
  PRICE_INCOMPLETE: '價格尚未計算，預算無法驗證；營養計算完成後才可採用。',
  VERSION_CONFLICT: '問卷已在此結果產生後修改，請重新生成後再採用。',
}
export const PRICE_SOURCE = '（假資料）示範超市'

// ---- 測試暗號 ----

const FLAG_WORDS = ['fail', 'slow', 'noknowever', 'noknow', 'nf', 'aimenu', 'overbudget', 'nonutri', 'noprice', 'allprice']

/** 口味偏好裡的英文暗號（前後不能緊接其他英文字母，避免 info 之類的字誤觸）。 */
export function flagsOf(taste: string): string[] {
  const text = taste.toLowerCase()
  const found = FLAG_WORDS.filter((word) => new RegExp(`(?<![a-z])${word}(?![a-z])`).test(text))
  if (found.includes('noknowever') && !found.includes('noknow')) found.push('noknow')
  return found
}

// ---- 結果內容 ----

type Dish = MealResult['dishes'][number]

function ingredientView(line: { food: Food; grams: number }): ResultIngredient {
  return {
    food_id: line.food.id,
    name: line.food.name,
    weight_basis: 'raw',
    whole_meal_grams: num(line.grams),
    display_grams: num(line.grams),
    group: line.food.seasoning ? '調味' : null,
    ...displayAmount(line.food, line.grams),
    llm_estimated: false,
    estimate_label: null,
    tfda_mapping_source: 'exact',
    tfda_sample_id: `FAKE-${line.food.id}`,
    llm_mapped: false,
    mapping_label: null,
    estimated_nutrients: [],
    approximate_nutrients: [],
    estimates: [],
  }
}

/** 假超市價格：整包花費（要買幾包）與按比例成本（用掉多少）是兩個數字，各自加總、不相加。 */
export function priceView(lines: { food: Food; grams: number }[], available: Set<string>, budget: string | null, at: number,
  options: { allKnown?: boolean; multiplier?: number } = {}): MealResult['prices'] {
  const totals = new Map<string, { food: Food; grams: number }>()
  for (const line of lines) {
    if (available.has(line.food.id)) continue // 家中已有的食材不列入價格
    const row = totals.get(line.food.id) ?? { food: line.food, grams: 0 }
    row.grams += line.grams
    totals.set(line.food.id, row)
  }
  const multiplier = options.multiplier ?? 1
  let knownPurchase = 0
  let knownCost = 0
  let complete = true
  const items = [...totals.values()].map(({ food: f, grams }, index) => {
    const pack = f.pack ?? (options.allKnown ? { grams: 300, price: 189 } : null)
    const base = {
      food_id: f.id, name: f.name, shortage_grams: num(grams),
      recipe_release_id: 'fake-recipes-1', price_data_release_id: 'fake-prices-1',
    }
    if (!pack) {
      complete = false
      return {
        ...base, price_status: 'unknown' as const, packs: null, purchase_price: null, cost: null,
        missing_reason: 'NO_PRICE_DATA', retailer: null, source_name: null, source: null, product_name: null,
        product_url: null, package_description: null, queried_at: null, valid_until: null, snapshot_id: null,
        usage_status: null,
      }
    }
    const packs = Math.ceil(grams / pack.grams)
    const purchase = packs * pack.price * multiplier
    const cost = (grams / pack.grams) * pack.price * multiplier
    knownPurchase += purchase
    knownCost += cost
    return {
      ...base, price_status: 'known' as const, packs, purchase_price: num(purchase), cost: num(cost),
      missing_reason: null, retailer: PRICE_SOURCE, source_name: PRICE_SOURCE, source: 'fake_market',
      product_name: `（假資料）${f.name} ${pack.grams} g`, product_url: `https://example.com/fake-market/${f.id}`,
      package_description: `${pack.grams} g／包`, queried_at: iso(at - 86_400_000), valid_until: iso(at + 6 * 86_400_000),
      snapshot_id: 9000 + index, usage_status: 'synthetic',
    }
  })
  const budgetStatus = budget !== null && knownCost > Number(budget) ? 'BUDGET_EXCEEDED' : complete ? 'OK' : 'PRICE_INCOMPLETE'
  return {
    status: 'computed',
    budget,
    budget_status: budgetStatus,
    price_complete: complete,
    unquantified_items: [],
    // 有缺價時總額就是未知，只另外給「已知部分」；不把缺的當成 0 加進去
    purchase_total: complete ? num(knownPurchase) : null,
    cost_total: complete ? num(knownCost) : null,
    known_purchase_total: num(knownPurchase),
    known_cost_total: num(knownCost),
    items,
  }
}

function knowledgeView(foodIds: string[], flags: string[]): MealResult['knowledge'] {
  if (flags.includes('noknow')) {
    return { status: 'unavailable', code: 'KNOWLEDGE_UNAVAILABLE', reason: 'SERVICE_UNAVAILABLE', items: [] }
  }
  if (flags.includes('nf')) return { status: 'not_found', items: [] }
  const items = knowledgeFor(foodIds).slice(0, 4).map((k) => {
    const id = foodByName(k.foodName).id
    return {
      ingredient_id: id,
      ingredient_name: k.foodName,
      explanation: k.fact,
      citations: [{
        knowledge_id: `KN-FAKE-${k.slug}`, source_version: 'fake-knowledge-1', ingredient_id: id, excerpt: k.excerpt,
        source_url: knowledgeUrl(k), rights: '合成內容，僅供畫面測試', conditions: null, source_name: KNOWLEDGE_SOURCE,
        title: k.title, published_at: k.published_at, content_provider: KNOWLEDGE_SOURCE,
        // 出處文字：畫面上一定要和說明一起顯示
        attribution: `資料來源：${KNOWLEDGE_SOURCE}〈${k.title}〉${k.published_at}`,
      }],
    }
  })
  return items.length ? { status: 'available', items } : { status: 'not_found', items: [] }
}

function linesOf(body: ResultBody): { food: Food; grams: number }[] {
  return body.dishes.flatMap((d) => d.ingredients)
    .map((i) => ({ food: food(i.food_id)!, grams: Number(i.whole_meal_grams) }))
    .filter((l) => l.food)
}

export type BuildInput = {
  householdId: string
  draft: MealRequestDraft
  members: Map<string, MemberRow>
  at: number
  /** 換一組菜（種子資料每天輪替） */
  rotation?: number
}
export type BuildOutcome =
  | { ok: true; mode: MealResult['mode']; fallbackReason: string | null; body: ResultBody }
  | { ok: false; failure: { code: string; message: string } }

const AI_DISH_LINES: [string, number][] = [['雞胸肉', 80], ['高麗菜', 80], ['蒜頭', 4], ['醬油', 5], ['沙拉油', 4]]

/** 產生一份生成結果（T14 的假版本）；算不出來回傳失敗原因，不硬湊數字。 */
export function buildResult(state: FakeState, input: BuildInput): BuildOutcome {
  const { draft, members } = input
  const flags = flagsOf(draft.taste_preferences)
  const targets = computeTargets(draft, members)
  if (targets.status !== 'available') {
    return { ok: false, failure: { code: 'GENERATION_FAILED', message: '無法計算本餐營養目標，請檢查成員資料後重新送出；問卷已保留。' } }
  }
  const menu = pickMenu(state, input.householdId, draft, members, { rotation: input.rotation, allowUnknown: true })
  if (!menu.ok) {
    const names = (menu.details.specified_dishes ?? []).map((n) => `「${n}」`).join('、')
    const messages: Record<string, string> = {
      SPECIFIED_DISH_DIETARY_CONFLICT: `指定的${names}含有成員過敏或禁吃的食材，請拿掉這道菜或確認成員的禁忌設定後重新送出。`,
      SPECIFIED_DISH_STAPLE_CONFLICT: `指定的${names}本身就是主食，和選的主食重複；請把主食改成「不需要主食」或拿掉這道菜。`,
    }
    return { ok: false, failure: messages[menu.code]
      ? { code: menu.code, message: messages[menu.code] }
      : { code: 'GENERATION_FAILED', message: '食譜庫裡符合條件的菜不夠，請減少道數或確認成員的禁忌設定後重新送出；問卷已保留。' } }
  }
  const excludedFoods = new Set([...members.values()].flatMap((m) => [...m.allergen_ingredient_ids, ...m.avoided_ingredient_ids]))
  const aiMode = flags.includes('aimenu') || menu.unknownSpecified.length > 0
  const fallbackReason = menu.unknownSpecified.length ? 'SPECIFIED_DISH_NOT_IN_DATABASE' : flags.includes('aimenu') ? 'NO_MATCHING_RECIPES' : null

  type Planned = DishInput & Pick<Dish, 'standard_dish_id' | 'dish_name' | 'aliases' | 'source' | 'recipe_id' | 'specified' | 'cooking_method' | 'steps'>
  const planned: Planned[] = [
    ...menu.picks.map((p) => ({
      key: p.recipe.recipe_id, lines: p.recipe.lines, standard_dish_id: p.recipe.standard_dish_id,
      dish_name: p.recipe.name, aliases: p.recipe.aliases, source: aiMode ? 'ai' as const : 'database' as const,
      recipe_id: aiMode ? null : p.recipe.recipe_id, specified: p.specified, cooking_method: p.recipe.method,
      steps: p.recipe.steps,
    })),
    ...menu.unknownSpecified.map((name, index) => ({
      key: `AI-${index + 1}`,
      lines: AI_DISH_LINES.map(([n, grams]) => ({ food: foodByName(n), grams })).filter((l) => !excludedFoods.has(l.food.id)),
      standard_dish_id: `DISH-FAKE-AI-${encodeURIComponent(name)}`, dish_name: name, aliases: [], source: 'ai' as const,
      recipe_id: null, specified: true, cooking_method: '炒',
      steps: ['（示範步驟）這道菜不在食譜庫，由假的「AI」配出食材。', '所有食材切好後下鍋炒熟，調味後起鍋。'],
    })),
  ]

  const stapleName = stapleLabel(draft.staple)
  const calc: MealCalc = calcMeal(planned.map((d) => ({ key: d.key, lines: d.lines })), stapleFood(stapleName), targets)
  if (flags.includes('nonutri')) {
    // 暗號 nonutri：假裝第一樣食材沒有熱量資料，整餐熱量變成未知
    const first = calc.dishes[0].lines[0]
    const energy = calc.totals.energy_kcal ?? 0
    calc.knownPartial.energy_kcal = energy - (first.grams * (first.food.per100.energy_kcal ?? 0)) / 100
    calc.missingByNutrient.energy_kcal = [first.food.name]
    calc.totals.energy_kcal = null
  }
  const within = withinRange(calc, targets)
  const blockCodes = within === null ? ['NUTRITION_INCOMPLETE'] : within ? [] : ['NUTRITION_OUT_OF_RANGE']
  const energy = calc.totals.energy_kcal
  const protein = calc.totals.protein_g

  const suggestions: NutritionSuggestion[] = []
  if (within === null) {
    const names = [...new Set([...(calc.missingByNutrient.energy_kcal ?? []), ...(calc.missingByNutrient.protein_g ?? [])])]
    suggestions.push({ code: 'NUTRITION_INCOMPLETE', action: '補齊這些食材的營養資料',
      ingredients: names.map((name) => ({ food_id: foodByName(name).id, name })) })
  } else if (!within) {
    if (energy !== null && energy > targets.energy * 1.1) {
      suggestions.push({ code: 'NUTRITION_OUT_OF_RANGE', nutrient: 'energy_kcal', direction: 'decrease',
        needed_change: num((energy - targets.energy * 1.1) / targets.totalN), within_adjustable_ranges: false, ingredients: [] })
    }
    if (protein !== null && protein < targets.protein * 0.9) {
      suggestions.push({ code: 'NUTRITION_OUT_OF_RANGE', nutrient: 'protein_g', direction: 'increase',
        needed_change: num((targets.protein * 0.9 - protein) / targets.totalN), within_adjustable_ranges: false, ingredients: [] })
    }
  }

  const dishes: Dish[] = planned.map((d, index) => {
    const lines = calc.dishes[index].lines
    const sodiumKnown = lines.every((l) => l.food.per100.sodium_mg !== null)
    const sodium = sodiumKnown ? lines.reduce((s, l) => s + (l.grams * (l.food.per100.sodium_mg ?? 0)) / 100, 0) / targets.totalN : null
    return {
      standard_dish_id: d.standard_dish_id, dish_name: d.dish_name, aliases: d.aliases, source: d.source,
      recipe_id: d.recipe_id, specified: d.specified, cooking_method: d.cooking_method, steps: d.steps,
      steps_display: d.steps, ingredients: lines.map(ingredientView),
      sodium_mg_per_serving: numOrNull(sodium), high_sodium: sodium === null ? null : sodium >= HIGH_SODIUM_DISH_MG,
    }
  })
  const names = new Map([...members.values()].map((m) => [m.id, m.display_name]))
  const gap: { energy_kcal?: string; protein_g?: string } = {}
  if (energy !== null) gap.energy_kcal = num(energy - targets.energy)
  if (protein !== null) gap.protein_g = num(protein - targets.protein)

  const context = menu.context
  const finalFoodIds = [...new Set(calc.dishes.flatMap((d) => d.lines.map((l) => l.food.id)))]
  const body: ResultBody = {
    sodium_adjustment: null,
    dishes,
    staple: calc.staple ? {
      label: stapleName ?? calc.staple.food.name, tfda_name: calc.staple.food.name, food_id: calc.staple.food.id,
      review_status: 'user_confirmed', unresolved_reason: null, whole_meal_grams: num(calc.staple.grams),
      display_amount: num(calc.staple.grams), display_unit: 'g', display_text: null, counts_for_price: false,
      llm_estimated: false,
    } : null,
    nutrition: {
      total_n: num(targets.totalN),
      target: { energy_kcal: num(targets.energy), protein_g: num(targets.protein) },
      energy_kcal: numOrNull(energy),
      protein_g: numOrNull(protein),
      gap,
      within_range: within === true,
      other_nutrients: Object.fromEntries(NUTRIENT_KEYS.slice(2).map((key) => [key, numOrNull(calc.totals[key])])),
      missing_nutrients: calc.missingByNutrient,
      known_partial: Object.fromEntries(Object.entries(calc.knownPartial).map(([key, value]) => [key, num(value)])),
      missing_by_nutrient: calc.missingByNutrient,
      quantities_verified: within === true,
      suggestions,
      llm_estimates: { used: false, food_ids: [], nutrients: [], label: null },
      llm_mappings: { used: false, food_ids: [], label: null },
      approximations: { used: false, food_ids: [], nutrients: [], basis: null, label: null },
      per_person: resultPeople(calc, targets, names),
      daily_reference: { ...DAILY_REFERENCE_META, high_sodium_dish_mg: String(HIGH_SODIUM_DISH_MG) },
    },
    prices: priceView(calc.dishes.flatMap((d) => d.lines), new Set(draft.available_ingredient_ids), draft.budget, input.at,
      { allKnown: flags.includes('allprice'), multiplier: flags.includes('overbudget') ? 20 : 1 }),
    knowledge: knowledgeView(finalFoodIds, flags),
    previous_day: {
      previous_date: context.previous_date,
      has_records: context.records.length > 0,
      excluded_dishes: context.excluded.filter((e) => !menu.specifiedExceptions.includes(e.standard_dish_id)),
      specified_repeats: menu.picks.filter((p) => menu.specifiedExceptions.includes(p.recipe.standard_dish_id)).map((p) => p.recipe.name),
    },
    knowledge_retry: null,
    price_retry: null,
    blockCodes,
    flags,
    targets: { rule_version: RULE_VERSION, total_n: num(targets.totalN), target: { energy_kcal: num(targets.energy), protein_g: num(targets.protein) } },
    requestSnapshot: clone(draft),
  }
  return { ok: true, mode: aiMode ? 'ai_generated_menu' : 'database_menu', fallbackReason, body }
}

// ---- 採用資格（meal_validation.evaluate 的假版本，決策表相同） ----

const block = (code: string, extra: Record<string, unknown> = {}): ResultBlock => ({ code, message: BLOCK_MESSAGES[code] ?? code, ...extra })

export function decisionsFor(state: FakeState, result: ResultRow): DecisionRow[] {
  return state.decisions.filter((d) => d.resultId === result.id).sort((a, b) => a.createdAt - b.createdAt)
}

export function evaluate(result: ResultRow, decisions: DecisionRow[], currentVersion: number | null) {
  const blocks: ResultBlock[] = []
  const pending: PendingDecision[] = []
  if (currentVersion !== null && result.requestVersion !== currentVersion) {
    blocks.push(block('VERSION_CONFLICT', { result_request_version: result.requestVersion, current_request_version: currentVersion }))
  }
  for (const code of result.body.blockCodes) blocks.push(block(code))
  if (result.body.blockCodes.includes('NUTRITION_INCOMPLETE') && !decisions.some((d) => d.kind === 'continue_missing_nutrition')) {
    pending.push({ kind: 'continue_missing_nutrition', message: '缺少必要營養資料：是否繼續查看此菜單？（仍不可採用）' })
  }
  const prices = result.body.prices
  let accepted = false
  if (prices.status !== 'computed') blocks.push(block('PRICE_INCOMPLETE', { reason: 'PRICE_NOT_COMPUTED' }))
  else if (prices.budget_status === 'BUDGET_EXCEEDED') {
    blocks.push(block('BUDGET_EXCEEDED', { known_cost_total: prices.known_cost_total, budget: prices.budget }))
  } else if (prices.budget_status === 'PRICE_INCOMPLETE') {
    // 缺價不擋採用：自動標示預算無法完整驗證，畫面列出查無價格的食材
    accepted = true
  }
  return { adoptable: blocks.length === 0, viewable: true, blocks, pending_decisions: pending, price_exception_accepted: accepted }
}

function statusAfter(result: ResultRow, decisions: DecisionRow[]): string {
  return evaluate(result, decisions, null).pending_decisions.length ? 'awaiting_decision' : 'result_ready'
}

function decisionView(d: DecisionRow): ResultDecision {
  return {
    id: d.id, kind: d.kind, result_version: d.resultVersion, request_version: d.requestVersion,
    missing_food_ids: d.missingFoodIds, produced_result_version: null, created_at: iso(d.createdAt),
  }
}

export function resultsOf(state: FakeState, requestId: string): ResultRow[] {
  return state.results.filter((r) => r.requestId === requestId).sort((a, b) => b.resultVersion - a.resultVersion)
}
export function findResult(state: FakeState, requestId: string, version: number): ResultRow {
  const row = state.results.find((r) => r.requestId === requestId && r.resultVersion === version)
  if (!row) throw new ApiFail(404, 'NOT_FOUND', '找不到此結果版本。')
  return row
}

export function eligibility(state: FakeState, request: RequestRow, result: ResultRow): MealResult['eligibility'] {
  const rows = decisionsFor(state, result)
  return { ...evaluate(result, rows, request.version), decisions: rows.map((d) => decisionView(d)) }
}

function retryState(job: JobRow | undefined): RetryState | null {
  if (!job) return null
  return {
    job_id: job.id, status: job.status, stage: job.stage, attempts: job.attempts, error: job.error,
    produced_result_version: job.status === 'succeeded' ? job.resultVersion : null, created_at: iso(job.createdAt),
  }
}
function latestRetry(state: FakeState, result: ResultRow, kind: JobRow['kind']): JobRow | undefined {
  return state.jobs.filter((j) => j.kind === kind && j.sourceResultId === result.id).sort((a, b) => b.createdAt - a.createdAt)[0]
}

/** 以目前的已採用紀錄重新比對昨日與同日其他餐（生成後歷史可能變動）。 */
export function previousDayCheck(state: FakeState, householdId: string, body: ResultBody, ignorePlanId?: string) {
  const draft = body.requestSnapshot
  if (!draft.meal_date) return { status: 'ok' as const, history_changed: false, conflicting: [] as string[], previous_date: '' }
  const context = previousContext(state, householdId, draft.meal_date, draft.meal_type, ignorePlanId)
  // 指定的菜本來就允許重複，不算衝突也不算歷史變動
  const specified = new Set(body.dishes.filter((d) => d.specified).map((d) => d.standard_dish_id))
  const ids = (list: { standard_dish_id: string }[]) =>
    list.map((e) => e.standard_dish_id).filter((id) => !specified.has(id)).sort().join(',')
  const now = new Set(context.excluded.map((e) => e.standard_dish_id))
  const conflicting = body.dishes.filter((d) => !d.specified && now.has(d.standard_dish_id)).map((d) => d.dish_name)
  return {
    status: conflicting.length ? 'conflict' as const : 'ok' as const,
    history_changed: ids(context.excluded) !== ids(body.previous_day.excluded_dishes),
    conflicting,
    previous_date: context.previous_date,
  }
}

export function resultView(state: FakeState, request: RequestRow, result: ResultRow): MealResult {
  const full = result.body
  // 只挑出對外的欄位；blockCodes、flags 等是假後端自己用的
  const body = {
    sodium_adjustment: full.sodium_adjustment, dishes: full.dishes, staple: full.staple, nutrition: full.nutrition,
    prices: full.prices, knowledge: full.knowledge, previous_day: full.previous_day,
    knowledge_retry: full.knowledge_retry, price_retry: full.price_retry,
  }
  const check = previousDayCheck(state, request.householdId, result.body, adoptedPlanId(state, request.id))
  return {
    meal_request_id: request.id,
    request_status: request.status,
    current_request_version: request.version,
    result_version: result.resultVersion,
    request_version: result.requestVersion,
    derived_from_result_version: result.derivedFromVersion,
    created_at: iso(result.createdAt),
    mode: result.mode,
    mode_label: MODE_LABELS[result.mode],
    fallback_reason: result.fallbackReason,
    fallback_message: result.fallbackReason
      ? FALLBACK_MESSAGES[result.fallbackReason] ?? '食譜資料庫沒有可行組合，已自動改由 AI 生成。' : null,
    ...clone(body),
    knowledge: { ...clone(body.knowledge), retry_available: body.knowledge.status === 'unavailable' },
    retries: { price: retryState(latestRetry(state, result, 'price_retry')), knowledge: retryState(latestRetry(state, result, 'knowledge_retry')) },
    previous_day_check: check.status === 'conflict'
      ? { status: 'conflict', history_changed: true, code: 'PREVIOUS_DAY_DISH_CONFLICT', conflicting_dishes: check.conflicting,
        previous_date: check.previous_date, message: '生成後昨日或同一天其他餐的已採用紀錄有變動，此菜單有重複的菜，不能採用；請重新生成。' }
      : { status: 'ok', history_changed: check.history_changed },
    eligibility: eligibility(state, request, result),
  }
}

function adoptedPlanId(state: FakeState, requestId: string): string | undefined {
  return state.plans.find((p) => p.requestId === requestId)?.id
}

export function resultSummaries(state: FakeState, request: RequestRow): MealResultSummary[] {
  return resultsOf(state, request.id).map((r) => ({
    result_version: r.resultVersion, request_version: r.requestVersion, mode: r.mode, mode_label: MODE_LABELS[r.mode],
    created_at: iso(r.createdAt), adoptable: eligibility(state, request, r).adoptable,
  }))
}

// ---- 缺值決策 ----

export function requireIdempotencyKey(headers: Record<string, string>): string {
  const key = headers['idempotency-key']
  if (!key) {
    throw new ApiFail(422, 'INVALID_INPUT', '輸入資料格式不正確。',
      { fields: [{ field: 'idempotency-key', reason: 'missing', message: 'Field required' }] })
  }
  if (key.length < 8 || key.length > 100) {
    throw new ApiFail(422, 'INVALID_INPUT', '輸入資料格式不正確。',
      { fields: [{ field: 'idempotency-key', reason: 'string_too_short', message: 'String should have at least 8 characters' }] })
  }
  return key
}

function checkVersions(request: RequestRow, result: ResultRow, requestVersion: number): void {
  if (request.status === 'queued' || request.status === 'running') {
    throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷正在生成中，請等待結果。', { reason: 'GENERATION_IN_PROGRESS' })
  }
  if (requestVersion !== request.version || result.requestVersion !== request.version) throw versionConflict()
}

/** 記錄使用者決定；同鍵同命令回傳同一筆，同一結果同一種決定只有一筆。 */
export function decide(state: FakeState, request: RequestRow, result: ResultRow, kind: DecisionKind, requestVersion: number,
  key: string): { decision: ResultDecision; created: boolean } {
  const sameKey = state.decisions.find((d) => d.householdId === request.householdId && d.idempotencyKey === key)
  if (sameKey) {
    if (sameKey.resultId !== result.id || sameKey.kind !== kind || sameKey.requestVersion !== requestVersion) {
      throw new ApiFail(409, 'IDEMPOTENCY_CONFLICT', '這個送出識別碼已用於不同的決定，請重新操作。')
    }
    return { decision: decisionView(sameKey), created: false }
  }
  checkVersions(request, result, requestVersion)
  const rows = decisionsFor(state, result)
  const same = rows.find((d) => d.kind === kind)
  if (same) return { decision: decisionView(same), created: false }
  const evaluation = evaluate(result, rows, request.version)
  if (!evaluation.pending_decisions.some((d) => d.kind === kind)) {
    throw new ApiFail(422, 'INVALID_INPUT', '此結果目前不需要（或不允許）這個決定。',
      { reason: 'DECISION_NOT_APPLICABLE', blocks: evaluation.blocks.map((b) => b.code) })
  }
  const row: DecisionRow = {
    id: newId(state, 'decision'), householdId: request.householdId, resultId: result.id, kind,
    resultVersion: result.resultVersion, requestVersion, missingFoodIds: null, idempotencyKey: key, createdAt: state.now(),
  }
  state.decisions.push(row)
  refreshStatus(state, request, result)
  return { decision: decisionView(row), created: true }
}

/** 只有最新結果版本、且問卷仍停在結果狀態時才更新（不覆蓋草稿、生成中或已採用）。 */
function refreshStatus(state: FakeState, request: RequestRow, result: ResultRow): void {
  const latest = resultsOf(state, request.id)[0]
  if ((request.status === 'result_ready' || request.status === 'awaiting_decision') && latest?.id === result.id) {
    request.status = statusAfter(result, decisionsFor(state, result))
    request.updatedAt = state.now()
  }
}

// ---- 工作 ----

const GENERATION_STAGES = ['started', 'validating_input', 'retrieving', 'validating', 'calculating', 'knowledge', 'explaining']
const AI_STAGES = ['started', 'validating_input', 'retrieving', 'ai_generating', 'validating', 'calculating', 'knowledge', 'explaining']
export const QUEUE_TIMEOUT_SECONDS = 300
export const ATTEMPT_TIMEOUT_SECONDS = 600

export function jobView(state: FakeState, job: JobRow): Job {
  return {
    id: job.id,
    kind: job.kind,
    status: job.status,
    stage: job.stage,
    attempts: job.attempts,
    max_attempts: 2,
    meal_request_id: job.requestId,
    input_version: job.inputVersion,
    source_result_version: job.sourceResultVersion,
    result_version: job.resultVersion,
    error: job.error,
    result_summary: job.status === 'succeeded' ? job.resultSummary : null,
    created_at: iso(job.createdAt),
    updated_at: iso(job.updatedAt),
    waited_seconds: Math.max(0, Math.floor((state.now() - job.createdAt) / 1000)),
    timeout_seconds: job.status === 'queued' ? QUEUE_TIMEOUT_SECONDS : job.status === 'running' ? ATTEMPT_TIMEOUT_SECONDS : null,
  }
}

function newJob(state: FakeState, request: RequestRow, kind: JobRow['kind'], key: string, timing: Pick<JobRow, 'queuedMs' | 'runMs' | 'stages' | 'failure'>,
  source: ResultRow | null): JobRow {
  const now = state.now()
  const job: JobRow = {
    id: newId(state, 'job'), householdId: request.householdId, kind, status: 'queued', stage: 'queued', attempts: 0,
    requestId: request.id, inputVersion: request.version, resultVersion: null, sourceResultId: source?.id ?? null,
    sourceResultVersion: source?.resultVersion ?? null, error: null, resultSummary: null, idempotencyKey: key,
    createdAt: now, updatedAt: now, ...timing,
  }
  state.jobs.push(job)
  return job
}

function sameKeyJob(state: FakeState, request: RequestRow, key: string, kind: JobRow['kind'], version: number,
  sourceId: string | null): JobRow | null {
  const existing = state.jobs.find((j) => j.householdId === request.householdId && j.idempotencyKey === key)
  if (!existing) return null
  if (existing.kind !== kind || existing.requestId !== request.id || existing.inputVersion !== version || existing.sourceResultId !== sourceId) {
    throw new ApiFail(409, 'IDEMPOTENCY_CONFLICT', '這個送出識別碼已用於不同的問卷內容，請重新送出。')
  }
  return existing
}

/** 送出生成：相同 Idempotency-Key 與相同問卷版本回傳同一工作。 */
export function submitGeneration(state: FakeState, request: RequestRow, version: number, key: string): { job: JobRow; created: boolean } {
  const existing = sameKeyJob(state, request, key, 'meal_generation', version, null)
  if (existing) return { job: existing, created: false }
  if (request.version !== version) throw new ApiFail(409, 'VERSION_CONFLICT', '問卷已更新，請確認最新內容後再送出。')
  if (request.status === 'queued' || request.status === 'running') {
    throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷正在生成中，請等待結果。', { reason: 'ALREADY_RUNNING' })
  }
  if (request.status === 'confirmed') throw alreadyAdopted()
  const members = householdMembers(state, request.householdId)
  if (checkRequest(request.draft, members).issues.length) {
    throw new ApiFail(422, 'INVALID_INPUT', '問卷尚未填寫完整，無法送出。', { reason: 'INCOMPLETE' })
  }
  const flags = flagsOf(request.draft.taste_preferences)
  const ai = flags.includes('aimenu')
  const job = newJob(state, request, 'meal_generation', key, {
    // 排隊約 2 秒 → 執行約 6 秒；slow 用來慢慢看進度與取消
    queuedMs: flags.includes('slow') ? 5000 : 2000,
    runMs: flags.includes('slow') ? 600_000 : flags.includes('fail') ? 3000 : 6000,
    stages: ai ? AI_STAGES : GENERATION_STAGES,
    failure: flags.includes('fail')
      ? { code: 'GENERATION_FAILED', message: '（假資料）生成失敗：模型服務沒有回應，已達自動重試上限；問卷已保留，可調整後重新送出。' }
      : null,
  }, null)
  request.status = 'queued'
  request.updatedAt = state.now()
  return { job, created: true }
}

/** 取消排隊中或執行中的生成：問卷回到送出前的狀態（同版本已有結果就回到結果，否則回草稿）。 */
export function cancelGeneration(state: FakeState, request: RequestRow): JobRow {
  const job = state.jobs.filter((j) => j.requestId === request.id && j.kind === 'meal_generation'
    && (j.status === 'queued' || j.status === 'running')).sort((a, b) => b.createdAt - a.createdAt)[0]
  if (!job) throw new ApiFail(409, 'VERSION_CONFLICT', '這份問卷目前沒有進行中的生成。', { reason: 'NOTHING_TO_CANCEL' })
  job.status = 'failed'
  job.stage = 'cancelled'
  job.error = { code: 'CANCELLED', message: '已取消生成；問卷已保留，可修改後重新送出。' }
  job.updatedAt = state.now()
  const previous = resultsOf(state, request.id).find((r) => r.requestVersion === job.inputVersion)
  request.status = previous ? statusAfter(previous, decisionsFor(state, previous)) : 'draft'
  request.updatedAt = state.now()
  return job
}

function submitRetry(state: FakeState, request: RequestRow, source: ResultRow, kind: 'knowledge_retry' | 'price_retry',
  requestVersion: number, key: string): { job: JobRow; created: boolean } {
  const existing = sameKeyJob(state, request, key, kind, requestVersion, source.id)
  if (existing) return { job: existing, created: false }
  if (request.status === 'confirmed') throw alreadyAdopted()
  if (request.status === 'queued' || request.status === 'running') {
    throw new ApiFail(409, 'VERSION_CONFLICT', '此問卷正在生成中，請等待結果。', { reason: 'GENERATION_IN_PROGRESS' })
  }
  if (requestVersion !== request.version || source.requestVersion !== request.version) {
    throw new ApiFail(409, 'VERSION_CONFLICT', '問卷已在此結果產生後修改，請重新生成。')
  }
  const active = state.jobs.find((j) => j.kind === kind && j.sourceResultId === source.id && (j.status === 'queued' || j.status === 'running'))
  if (active) return { job: active, created: false }
  const flags = source.body.flags
  const failure = kind === 'price_retry' && flags.includes('noprice')
    ? { code: 'PRICE_LOOKUP_FAILED', message: '缺價食材的定向查價失敗；原結果不變，可稍後再試或接受缺價例外。' }
    : kind === 'knowledge_retry' && flags.includes('noknowever')
      ? { code: 'KNOWLEDGE_UNAVAILABLE', message: '（假資料）營養知識服務仍然無法使用；原結果不變，可稍後再試。' }
      : null
  const stages = kind === 'price_retry' ? ['started', 'calculating'] : ['started', 'knowledge', 'explaining']
  return { job: newJob(state, request, kind, key, { queuedMs: 1000, runMs: 3000, stages, failure }, source), created: true }
}

export function requestKnowledgeRetry(state: FakeState, request: RequestRow, source: ResultRow, requestVersion: number, key: string) {
  if (source.body.knowledge.status !== 'unavailable') {
    throw new ApiFail(422, 'INVALID_INPUT', '此結果的知識說明不需要重試。', { reason: 'RETRY_NOT_APPLICABLE' })
  }
  return submitRetry(state, request, source, 'knowledge_retry', requestVersion, key)
}

export function requestPriceRetry(state: FakeState, request: RequestRow, source: ResultRow, requestVersion: number, key: string) {
  const prices = source.body.prices
  if (prices.status !== 'computed' || prices.budget_status !== 'PRICE_INCOMPLETE') {
    throw new ApiFail(422, 'INVALID_INPUT', '此結果目前不需要（或不允許）這個決定。',
      { reason: 'DECISION_NOT_APPLICABLE', blocks: evaluate(source, [], request.version).blocks.map((b) => b.code) })
  }
  return submitRetry(state, request, source, 'price_retry', requestVersion, key)
}

export function addResult(state: FakeState, request: RequestRow | null, householdId: string, requestVersion: number, at: number,
  built: Extract<BuildOutcome, { ok: true }>, derivedFrom: ResultRow | null): ResultRow {
  const version = request ? (resultsOf(state, request.id)[0]?.resultVersion ?? 0) + 1 : 1
  const row: ResultRow = {
    id: newId(state, 'result'), requestId: request?.id ?? null, householdId, resultVersion: version, requestVersion,
    derivedFromVersion: derivedFrom?.resultVersion ?? null, createdAt: at, mode: built.mode,
    fallbackReason: built.fallbackReason, body: built.body,
  }
  state.results.push(row)
  return row
}

function failJob(job: JobRow, at: number, failure: { code: string; message: string }): void {
  job.status = 'failed'
  job.stage = 'failed'
  job.error = failure
  job.updatedAt = at
}

function finishJob(state: FakeState, job: JobRow, at: number): void {
  const request = state.requests.find((r) => r.id === job.requestId)
  if (!request) return failJob(job, at, { code: 'GENERATION_FAILED', message: '問卷已不存在。' })
  job.attempts = Math.max(job.attempts, 1)
  if (job.kind === 'meal_generation') {
    const stillMine = (request.status === 'queued' || request.status === 'running') && request.version === job.inputVersion
    const built: BuildOutcome = job.failure
      ? { ok: false, failure: job.failure }
      : buildResult(state, { householdId: request.householdId, draft: request.draft, members: householdMembers(state, request.householdId), at })
    if (!built.ok) {
      failJob(job, at, built.failure)
      if (job.failure) job.attempts = 2
      if (stillMine) {
        request.status = 'failed'
        request.updatedAt = at
      }
      return
    }
    const result = addResult(state, request, request.householdId, job.inputVersion, at, built, null)
    const status = statusAfter(result, [])
    job.status = 'succeeded'
    job.stage = 'result_ready'
    job.resultVersion = result.resultVersion
    job.resultSummary = {
      generation_result_id: result.id, result_version: result.resultVersion, mode: result.mode,
      mode_label: MODE_LABELS[result.mode], fallback_reason: result.fallbackReason,
      request_status: status as 'result_ready' | 'awaiting_decision',
    }
    job.updatedAt = at
    if (stillMine) {
      request.status = status
      request.updatedAt = at
    }
    return
  }
  // 結果重試：以來源版本為基礎產生新版本，菜單與營養不變
  const source = state.results.find((r) => r.id === job.sourceResultId)
  if (!source) return failJob(job, at, { code: 'RESULT_NOT_FOUND', message: '找不到要重試的結果版本。' })
  if (job.failure) return failJob(job, at, job.failure)
  const body = clone(source.body)
  if (job.kind === 'knowledge_retry') {
    const foodIds = [...new Set(body.dishes.flatMap((d) => d.ingredients.map((i) => i.food_id)))]
    body.knowledge = knowledgeView(foodIds, body.flags.filter((f) => f !== 'noknow'))
    body.knowledge_retry = { from_result_version: source.resultVersion, retried_at: iso(at) }
    body.flags = body.flags.filter((f) => f !== 'noknow')
  } else {
    const draft = body.requestSnapshot
    body.prices = priceView(linesOf(body), new Set(draft.available_ingredient_ids), draft.budget, at,
      { allKnown: true, multiplier: body.flags.includes('overbudget') ? 20 : 1 })
    body.price_retry = { from_result_version: source.resultVersion, retried_at: iso(at), price_source: PRICE_SOURCE }
  }
  const result = addResult(state, request, request.householdId, source.requestVersion, at,
    { ok: true, mode: source.mode, fallbackReason: source.fallbackReason, body }, source)
  job.status = 'succeeded'
  job.stage = 'result_ready'
  job.resultVersion = result.resultVersion
  job.updatedAt = at
  refreshStatus(state, request, result)
}

/**
 * 依「送出後經過的時間」推進所有進行中的工作：排隊 → 執行（階段會變）→ 成功並產生結果版本（或失敗）。
 * 每次處理請求前呼叫一次，所以不需要背景計時器，測試換一個假時鐘就能快轉。
 */
export function advanceJobs(state: FakeState): void {
  const now = state.now()
  for (const job of [...state.jobs].sort((a, b) => a.createdAt - b.createdAt)) {
    if (job.status !== 'queued' && job.status !== 'running') continue
    const elapsed = now - job.createdAt
    if (elapsed < job.queuedMs) continue
    const request = state.requests.find((r) => r.id === job.requestId)
    if (elapsed < job.queuedMs + job.runMs) {
      const index = Math.min(job.stages.length - 1, Math.floor(((elapsed - job.queuedMs) / job.runMs) * job.stages.length))
      if (job.status !== 'running' || job.stage !== job.stages[index]) {
        job.status = 'running'
        job.attempts = Math.max(job.attempts, 1)
        job.stage = job.stages[index]
        job.updatedAt = job.createdAt + job.queuedMs + Math.floor((index * job.runMs) / job.stages.length)
      }
      if (job.kind === 'meal_generation' && request && request.status === 'queued' && request.version === job.inputVersion) {
        request.status = 'running'
        request.updatedAt = job.createdAt + job.queuedMs
      }
      continue
    }
    finishJob(state, job, job.createdAt + job.queuedMs + job.runMs)
  }
}
