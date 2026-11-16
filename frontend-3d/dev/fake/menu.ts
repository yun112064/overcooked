import type { KnowledgeTips, MealRequestDraft, MealType, NutritionPreview, RecipeRetrieval, RecipeSummary } from '../../src/api/types.ts'
import {
  DESSERT, KNOWLEDGE_SOURCE, MAIN, RECIPES, SOUP, STAPLE, VEGETABLE, foodByName, knowledgeFor, knowledgeUrl,
  mainIngredients, recipe, recipeByName, recipeSummary,
} from './catalog.ts'
import type { Recipe } from './catalog.ts'
import { ApiFail, addDays, num, ratio } from './core.ts'
import type { FakeState, MemberRow, PlanRow } from './core.ts'
import {
  DAILY_REFERENCE_META, calcMeal, computeTargets, displayAmount, knownPartialView, previewParticipations, previewPeople, stapleFood,
  totalsView, withinRange,
} from './nutrition.ts'
import type { MealCalc, Targets } from './nutrition.ts'
import { restrictions, stapleLabel, today } from './requests.ts'

/** 配菜（T11 的假版本）、昨日去重與資料庫菜單的營養預覽（T12 的假版本）。 */

export function planDate(state: FakeState, plan: PlanRow): string {
  return typeof plan.date === 'string' ? plan.date : addDays(today(state), -plan.date.daysAgo)
}

export type ExcludedDish = { standard_dish_id: string; dish_name: string; aliases: string[]; from: string }
export type PreviousContext = {
  previous_date: string
  records: { id: string; meal_type: string }[]
  excluded: ExcludedDish[]
}

/** 比對範圍：前一個日曆日的全部餐別，加上同一天其他餐別已採用的菜。 */
export function previousContext(state: FakeState, householdId: string, mealDate: string, mealType: MealType | null,
  ignorePlanId?: string): PreviousContext {
  const previous = addDays(mealDate, -1)
  const records: PreviousContext['records'] = []
  const excluded: ExcludedDish[] = []
  for (const plan of state.plans) {
    if (plan.householdId !== householdId || plan.id === ignorePlanId) continue
    const date = planDate(state, plan)
    const from = date === previous ? 'previous_day' : date === mealDate && plan.mealType !== mealType ? plan.mealType : null
    if (!from) continue
    if (from === 'previous_day') records.push({ id: plan.id, meal_type: plan.mealType })
    for (const dish of plan.dishes) {
      if (!excluded.some((e) => e.standard_dish_id === dish.standard_dish_id)) {
        excluded.push({ standard_dish_id: dish.standard_dish_id, dish_name: dish.dish_name, aliases: dish.aliases, from })
      }
    }
  }
  return { previous_date: previous, records, excluded }
}

type Slot = 'seafood' | 'veg' | 'soup' | 'meat' | 'stapleDish' | 'dessert' | 'egg'

function slotOf(r: Recipe): Slot {
  if (r.category === SOUP) return 'soup'
  if (r.category === STAPLE) return 'stapleDish'
  if (r.category === DESSERT) return 'dessert'
  const tags = mainIngredients(r.lines.map((l) => l.food.name))
  if (tags.includes('海鮮')) return 'seafood'
  if (tags.includes(VEGETABLE) && !tags.includes('蛋') && !tags.includes('豆腐')) return 'veg'
  if (tags.includes(VEGETABLE)) return 'egg'
  return 'meat'
}

const SLOTS_WITH_STAPLE: Slot[] = ['seafood', 'veg', 'soup', 'meat', 'egg', 'veg', 'meat', 'soup', 'seafood', 'veg']
const SLOTS_NO_STAPLE: Slot[] = ['seafood', 'veg', 'soup', 'stapleDish', 'meat', 'egg', 'veg', 'soup', 'meat', 'seafood']
const SLOTS_BREAKFAST: Slot[] = ['stapleDish', 'egg', 'dessert', 'soup', 'veg', 'meat', 'veg', 'soup', 'egg', 'dessert']

export type MenuFailure = {
  ok: false
  status: string
  code: string
  reason: string
  details: { specified_dishes?: string[]; staple?: string | null; conflicts?: { dish_name: string; food_ids: string[] }[] }
  context: PreviousContext
}
export type MenuSuccess = {
  ok: true
  picks: { recipe: Recipe; specified: boolean }[]
  /** 指定但食譜庫沒有的菜名（生成時改由「AI」處理） */
  unknownSpecified: string[]
  context: PreviousContext
  specifiedExceptions: string[]
}

/**
 * 依問卷挑菜：指定菜優先，其餘照固定的菜色順序補滿；rotation 用來換下一組（不採用、或每天輪替）。
 * allowUnknown：生成流程允許食譜庫沒有的指定菜（改走 AI 模式），資料庫菜單預覽則直接回報找不到。
 */
export function pickMenu(state: FakeState, householdId: string, draft: MealRequestDraft, members: Map<string, MemberRow>,
  options: { rotation?: number; allowUnknown?: boolean; ignorePlanId?: string } = {}): MenuSuccess | MenuFailure {
  const rotation = options.rotation ?? 0
  const excludedFoods = new Set(restrictions(draft, members).excluded_food_ids)
  const context = previousContext(state, householdId, draft.meal_date ?? today(state), draft.meal_type, options.ignorePlanId)
  const hasStaple = stapleLabel(draft.staple) !== null
  const fail = (status: string, code: string, reason: string, details: MenuFailure['details']): MenuFailure =>
    ({ ok: false, status, code, reason, details, context })

  const picks: { recipe: Recipe; specified: boolean }[] = []
  const unknown: string[] = []
  for (const name of draft.specified_dishes) {
    const found = recipeByName(name)
    if (!found) unknown.push(name)
    else if (!picks.some((p) => p.recipe === found)) picks.push({ recipe: found, specified: true })
  }
  if (unknown.length && !options.allowUnknown) {
    return fail('no_feasible_database_menu', 'NO_FEASIBLE_DATABASE_MENU', 'SPECIFIED_DISH_NOT_IN_DATABASE', { specified_dishes: unknown })
  }
  const conflicts = picks
    .map((p) => ({ dish_name: p.recipe.name, food_ids: p.recipe.lines.map((l) => l.food.id).filter((id) => excludedFoods.has(id)) }))
    .filter((c) => c.food_ids.length)
  if (conflicts.length) {
    return fail('specified_dish_conflict', 'SPECIFIED_DISH_DIETARY_CONFLICT', 'SPECIFIED_DISH_DIETARY_CONFLICT',
      { specified_dishes: conflicts.map((c) => c.dish_name), conflicts })
  }
  const stapleClash = picks.filter((p) => hasStaple && p.recipe.category === STAPLE).map((p) => p.recipe.name)
  if (stapleClash.length) {
    // 有選主食又指定主食類的菜：回報讓使用者擇一，不默默丟掉指定菜
    return fail('specified_dish_staple_conflict', 'SPECIFIED_DISH_STAPLE_CONFLICT', 'SPECIFIED_DISH_STAPLE_CONFLICT',
      { specified_dishes: stapleClash, staple: draft.staple })
  }

  const yesterday = new Set(context.excluded.map((e) => e.standard_dish_id))
  const allowed = (r: Recipe) =>
    !picks.some((p) => p.recipe === r) && !yesterday.has(r.standard_dish_id)
    && !r.lines.some((l) => excludedFoods.has(l.food.id))
    && (draft.meal_type === 'breakfast' || r.meal_type !== '早餐')
    && !(hasStaple && r.category === STAPLE)
  const wanted = Math.max((draft.dish_count ?? picks.length + unknown.length) - unknown.length, picks.length)
  const order = draft.meal_type === 'breakfast' ? SLOTS_BREAKFAST : hasStaple ? SLOTS_WITH_STAPLE : SLOTS_NO_STAPLE
  const taken = picks.map((p) => slotOf(p.recipe))
  const used = new Map<Slot, number>()
  for (const slot of order) {
    if (picks.length >= wanted) break
    // 指定菜已經占掉的菜色不再補一道
    const skip = taken.indexOf(slot)
    if (skip !== -1) {
      taken.splice(skip, 1)
      continue
    }
    const pool = RECIPES.filter((r) => slotOf(r) === slot && allowed(r))
    const fallback = pool.length ? pool : RECIPES.filter((r) => r.category === MAIN && allowed(r))
    if (!fallback.length) continue
    const turn = used.get(slot) ?? 0
    used.set(slot, turn + 1)
    picks.push({ recipe: fallback[(rotation + turn) % fallback.length], specified: false })
  }
  if (picks.length < wanted) {
    const reason = yesterday.size ? 'ALL_EXCLUDED_BY_PREVIOUS_DAY' : 'NO_MATCHING_RECIPES'
    return fail('no_feasible_database_menu', 'NO_FEASIBLE_DATABASE_MENU', reason, {})
  }
  return {
    ok: true, picks, unknownSpecified: unknown, context,
    specifiedExceptions: picks.filter((p) => p.specified && yesterday.has(p.recipe.standard_dish_id)).map((p) => p.recipe.standard_dish_id),
  }
}

const MAX_ALTERNATIVES = 5

function contextView(context: PreviousContext, specifiedExceptions: string[]): NonNullable<RecipeRetrieval['previous_day']> {
  return {
    previous_date: context.previous_date,
    records: context.records,
    excluded_dishes: context.excluded.filter((e) => !specifiedExceptions.includes(e.standard_dish_id))
      .map((e) => ({ standard_dish_id: e.standard_dish_id, dish_name: e.dish_name })),
    specified_exceptions: specifiedExceptions,
  }
}

/** 資料庫菜單（GET /meal-requests/{id}/recipes）：略過這一版問卷已「不採用」的組合。 */
export function databaseMenu(state: FakeState, row: { id: string; householdId: string; version: number; draft: MealRequestDraft },
  members: Map<string, MemberRow>): Omit<RecipeRetrieval, 'input_version' | 'fallback_eligible' | 'adopted_plan'> {
  const rejected = state.rejections.filter((r) => r.requestId === row.id && r.version === row.version)
    .map((r) => [...r.recipeIds].sort().join(','))
  const combos: RecipeSummary[][] = []
  let context: PreviousContext | null = null
  let exceptions: string[] = []
  for (let rotation = 0; rotation < MAX_ALTERNATIVES; rotation += 1) {
    const menu = pickMenu(state, row.householdId, row.draft, members, { rotation })
    if (!menu.ok) {
      return {
        status: menu.status, code: menu.code, reason: menu.reason, details: menu.details,
        previous_day: contextView(menu.context, []), selected: [], candidates: [], rejected_count: rejected.length,
      }
    }
    context = menu.context
    exceptions = menu.specifiedExceptions
    const combo = menu.picks.map((p) => recipeSummary(p.recipe, p.specified))
    const key = combo.map((r) => r.recipe_id).sort().join(',')
    if (!combos.some((c) => c.map((r) => r.recipe_id).sort().join(',') === key)) combos.push(combo)
  }
  const remaining = combos.filter((c) => !rejected.includes(c.map((r) => r.recipe_id).sort().join(',')))
  const selected = remaining[0] ?? []
  const selectedIds = new Set(selected.map((r) => r.recipe_id))
  const candidates = remaining.slice(1).flat().filter((r, i, all) =>
    !selectedIds.has(r.recipe_id) && all.findIndex((x) => x.recipe_id === r.recipe_id) === i)
  return {
    status: remaining.length ? 'database_menu' : 'all_menus_rejected',
    code: null,
    reason: remaining.length ? null : 'ALL_MENUS_REJECTED',
    details: {},
    previous_day: contextView(context!, exceptions),
    selected,
    candidates,
    rejected_count: rejected.length,
  }
}

// ---- T12 營養預覽 ----

export type PreviewCalc = { preview: NutritionPreview; calc: MealCalc; recipes: Recipe[] }

function ingredientGroup(isSeasoning: boolean, index: number): '主食材' | '佐料' | '調味' {
  if (isSeasoning) return '調味'
  return index === 0 ? '主食材' : '佐料'
}

export function nutritionPreview(draft: MealRequestDraft, members: Map<string, MemberRow>, recipeIds: string[]): PreviewCalc {
  const recipes = recipeIds.map((id) => {
    const found = recipe(id)
    if (!found) throw new ApiFail(503, 'RECIPE_NUTRITION_RELEASE_MISMATCH', '所選食譜不屬於目前的營養資料發布。')
    return found
  })
  const targets: Targets = computeTargets(draft, members)
  if (targets.status !== 'available') {
    // 成員資料算不出目標時無法驗證營養（真後端在這裡的確切錯誤碼沒有對照過，見 dev/README.md）
    throw new ApiFail(503, 'NUTRITION_DATA_UNAVAILABLE',
      targets.status === 'unavailable' ? targets.message : '無法計算本餐營養目標，請檢查成員資料。')
  }
  const stapleName = stapleLabel(draft.staple)
  const calc = calcMeal(recipes.map((r) => ({ key: r.recipe_id, lines: r.lines })), stapleFood(stapleName), targets)
  const within = withinRange(calc, targets)
  const status: NutritionPreview['status'] = within === null ? 'calculation_incomplete' : within ? 'nutrition_passed' : 'outside_tolerance'
  const missing = ['energy_kcal', 'protein_g'].filter((key) => calc.totals[key as 'energy_kcal'] === null)
  const names = new Map([...members.values()].map((m) => [m.id, m.display_name]))
  const preview: NutritionPreview = {
    status,
    status_message: status === 'nutrition_passed'
      ? '熱量與蛋白質都在標準內（熱量不超過目標 110%、蛋白質不低於目標 90%）。'
      : status === 'outside_tolerance'
        ? '熱量超過目標 110% 或蛋白質低於目標 90%，可查看但不能採用；可調整指定菜色、道數或主食後再試。'
        : '缺少熱量或蛋白質的必要營養資料，計算不完整，不能採用。',
    dish_portions: { factors: Object.fromEntries(recipes.map((r) => [r.recipe_id, ratio(calc.scale)])) },
    dishes: calc.dishes.map((d, dishIndex) => ({
      recipe_id: d.key,
      ingredients: d.lines.map((l, index) => ({
        name: l.food.name, food_id: l.food.id, unit: 'g', grams: num(l.grams), line_id: `${d.key}-L${index + 1}`,
        group: ingredientGroup(l.food.seasoning, index), ...displayAmount(l.food, l.grams),
      })),
      steps: recipes[dishIndex].steps,
      adjusted: Math.abs(calc.scale - 1) > 0.01,
    })),
    staple: calc.staple ? { name: stapleName ?? calc.staple.food.name, final_grams: num(calc.staple.grams) } : null,
    per_person: previewParticipations(calc, targets, recipeIds),
    targets: { energy_kcal: num(targets.energy), protein_g: num(targets.protein) },
    input_issues: [],
    adoption_blocked: false,
    final_food_ids: [...new Set(calc.dishes.flatMap((d) => d.lines.map((l) => l.food.id)))],
    nutrition_passed: status === 'nutrition_passed',
    nutrition: { totals: totalsView(calc), missing },
    people: previewPeople(calc, targets, recipeIds, names),
    daily_reference_meta: DAILY_REFERENCE_META,
    nutrition_all: { totals: totalsView(calc), known_partial: knownPartialView(calc), missing_by_nutrient: calc.missingByNutrient },
  }
  return { preview, calc, recipes }
}

/** 營養小知識（POST /meal-requests/{id}/knowledge-tips）：出處一定跟著文字一起回傳。 */
export function knowledgeTips(foodIds: string[], flags: string[], nutritionStatus: string): KnowledgeTips {
  if (flags.includes('noknow')) {
    return { status: 'unavailable', reason: 'KNOWLEDGE_UNAVAILABLE', items: [], dropped: [], not_found: [],
      knowledge_status: 'unavailable', nutrition_status: nutritionStatus }
  }
  const entries = flags.includes('nf') ? [] : knowledgeFor(foodIds).slice(0, 4)
  const items: KnowledgeTips['items'] = entries.map((k) => ({
    ingredient_id: foodByName(k.foodName).id,
    ingredient_name: k.foodName,
    tip_zh: k.fact,
    citations: [{
      knowledge_id: `KN-FAKE-${k.slug}`, source_url: knowledgeUrl(k), source_label: KNOWLEDGE_SOURCE, conditions: null,
    }],
  }))
  const covered = new Set(items.map((i) => i.ingredient_id))
  return {
    status: items.length ? 'available' : 'not_found',
    items,
    dropped: [],
    not_found: foodIds.filter((id) => !covered.has(id)),
    knowledge_status: items.length ? 'available' : 'not_found',
    nutrition_status: nutritionStatus,
  }
}
