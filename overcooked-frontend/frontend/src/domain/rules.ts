import type { AdoptedMeal, Draft, Ingredient, Member, Plan, PurchaseRow, Recipe, Scenario, ServingRow } from './types.ts'

export function localDate(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
export function previousDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day - 1))
  return date.toISOString().slice(0, 10)
}
export const round = (value: number, precision = 2) => Math.round((value + Number.EPSILON) * 10 ** precision) / 10 ** precision
export const money = (value: number | null) => value == null ? '未取得' : `NT$${value.toLocaleString('zh-TW', { maximumFractionDigits: 2 })}`
export function createDraft(householdId: string, members: Member[], date = localDate()): Draft {
  return { id: crypto.randomUUID(), householdId, date, meal: 'dinner', participation: members.filter(member => member.householdId === householdId).map(member => ({ memberId: member.id, dining: true, bento: false, bentoMeal: 'lunch' })), budget: null, staple: '', dishCount: 3, tastes: [], requested: [], available: [], revision: 0, updatedAt: new Date().toISOString() }
}
export function participantItems(draft: Draft, members: Member[]) {
  return draft.participation.flatMap(item => {
    const member = members.find(person => person.id === item.memberId && person.householdId === draft.householdId)
    if(!member) return []
    return [...(item.dining ? [{ member, meal: draft.meal, bento: false }] : []), ...(item.bento ? [{ member, meal: item.bentoMeal, bento: true }] : [])]
  })
}
export const totalPortions = (draft: Draft, members: Member[]) => round(participantItems(draft, members).reduce((sum, item) => sum + item.member.portion, 0))
export const fingerprint = (draft: Draft) => JSON.stringify(draft)
export function memberFingerprint(draft: Draft, members: Member[]) {
  const ids = new Set(participantItems(draft, members).map(item => item.member.id))
  return JSON.stringify(members.filter(member => ids.has(member.id)).sort((a, b) => a.id.localeCompare(b.id)).map(member => [member.id, member.name, member.age, member.sex, member.weight, member.activity, member.portion, member.goal, [...member.allergies].sort(), [...member.forbidden].sort()]))
}
export function validateDraft(draft: Draft, members: Member[]): string[] {
  const errors: string[] = []
  if(!/^\d{4}-\d{2}-\d{2}$/.test(draft.date) || Number.isNaN(Date.parse(`${draft.date}T00:00:00Z`)) || new Date(`${draft.date}T00:00:00Z`).toISOString().slice(0, 10) !== draft.date) errors.push('請選擇有效的餐食日期。')
  if(!participantItems(draft, members).length) errors.push('請至少選擇一位用餐或便當成員。')
  if(draft.budget == null || !Number.isFinite(draft.budget) || draft.budget < 0) errors.push('請輸入有效的預算。')
  if(!draft.staple) errors.push('請選擇主食。')
  if(!Number.isInteger(draft.dishCount) || draft.dishCount < 1) errors.push('菜數須為大於 0 的整數。')
  if(draft.requested.length > draft.dishCount) errors.push('指定菜色數量不能超過菜數。')
  return errors
}
export function exclusions(draft: Draft, records: AdoptedMeal[]): Set<string> {
  const result = new Set<string>()
  records.filter(record => record.plan.householdId === draft.householdId && record.plan.date === previousDate(draft.date)).forEach(record => record.plan.recipes.filter(recipe => recipe.kind === 'dish').forEach(recipe => { if(!draft.requested.includes(recipe.id)) result.add(recipe.id) }))
  return result
}
export function purchaseRows(recipes: Recipe[], n: number, available: string[], catalog: Ingredient[]): PurchaseRow[] {
  const totals = new Map<string, number>()
  recipes.forEach(recipe => recipe.ingredients.forEach(item => totals.set(item.ingredientId, (totals.get(item.ingredientId) ?? 0) + item.amount * n)))
  return Array.from(totals.entries()).filter(([id]) => !available.includes(id)).map(([id, amount]) => {
    const ingredient = catalog.find(item => item.id === id)
    if(!ingredient) throw new Error('食材資料不完整，請修改條件。')
    const packages = Math.ceil(amount / ingredient.packageAmount)
    return { ingredientId: id, name: ingredient.name, amount: round(amount), unit: ingredient.unit, packageAmount: ingredient.packageAmount, packagePrice: ingredient.packagePrice, packages, purchaseCost: packages * ingredient.packagePrice, mealCost: amount / ingredient.packageAmount * ingredient.packagePrice }
  })
}
export function nutritionErrors(plan: Pick<Plan, 'nutrition'>): string[] {
  const errors: string[] = []
  const { energy, protein, energyTarget, proteinTarget } = plan.nutrition
  if(energy == null || protein == null) errors.push('缺少必要營養資料，目前無法採用。')
  else {
    if(Math.abs(energy - energyTarget) > energyTarget * 0.1 + 1e-8) errors.push('熱量估算超出目標 ±10%。')
    if(Math.abs(protein - proteinTarget) > proteinTarget * 0.1 + 1e-8) errors.push('蛋白質估算超出目標 ±10%。')
  }
  return errors
}
export function eligibility(plan: Plan): string[] {
  return [
    ...(plan.mealCost > plan.budget ? [`本餐成本 ${money(plan.mealCost)} 超出預算 ${money(plan.budget)}。`] : []),
    ...nutritionErrors(plan),
  ]
}
function combinations<T>(items: T[], count: number): T[][] {
  if(count === 0) return [[]]
  if(count > items.length || count < 0) return []
  return items.flatMap((item, index) => combinations(items.slice(index + 1), count - 1).map(rest => [item, ...rest]))
}
export class PlannerError extends Error {
  kind: 'service' | 'no-solution' | 'validation'
  constructor(kind: PlannerError['kind'], message: string) { super(message); this.name = 'PlannerError'; this.kind = kind }
}
export function generateDemoPlan(draft: Draft, members: Member[], records: AdoptedMeal[], recipes: Recipe[], staple: Recipe | undefined, catalog: Ingredient[], scenario: Scenario = 'normal'): Plan {
  const errors = validateDraft(draft, members)
  if(errors.length) throw new PlannerError('validation', errors.join('\n'))
  if(scenario === 'service-failure') throw new PlannerError('service', '暫時無法連接菜單服務，請稍後重試。你的設定已保留。')
  if(scenario === 'no-solution') throw new PlannerError('no-solution', '目前預算與飲食條件下，沒有符合需求的組合。')
  const items = participantItems(draft, members), n = totalPortions(draft, members)
  const forbidden = new Set(items.flatMap(item => [...item.member.allergies, ...item.member.forbidden]))
  const excluded = exclusions(draft, records)
  const allowed = (recipe: Recipe) => !excluded.has(recipe.id) && recipe.ingredients.every(item => !forbidden.has(item.ingredientId))
  const requested = draft.requested.map(id => recipes.find(recipe => recipe.id === id))
  if(requested.some(recipe => !recipe)) throw new PlannerError('validation', '找不到指定菜色，請重新選擇。')
  const mandatory = requested.filter((recipe): recipe is Recipe => !!recipe)
  if(mandatory.some(recipe => !allowed(recipe)) || (staple && !allowed(staple))) throw new PlannerError('no-solution', '指定菜色或主食與本次成員的過敏、禁吃食材衝突。')
  const pool = recipes.filter(recipe => allowed(recipe) && !draft.requested.includes(recipe.id))
  for(const source of ['database_menu', 'ai_generated_menu'] as const) {
    const candidates = pool.filter(recipe => source === 'ai_generated_menu' || recipe.source === 'database_menu')
    for(const choice of combinations(candidates, draft.dishCount - mandatory.length)) {
      const dishes = [...mandatory, ...choice]
      if(source === 'database_menu' && dishes.some(recipe => recipe.source !== source)) continue
      if(source === 'ai_generated_menu' && dishes.every(recipe => recipe.source === 'database_menu')) continue
      const menu = [...dishes, ...(staple ? [staple] : [])]
      const purchases = purchaseRows(menu, n, draft.available, catalog)
      const mealCost = round(purchases.reduce((sum, row) => sum + (row.mealCost ?? 0), 0))
      const nutrition = { energy: round(menu.reduce((sum, recipe) => sum + recipe.energyPerN * n, 0)), protein: round(menu.reduce((sum, recipe) => sum + recipe.proteinPerN * n, 0)), energyTarget: round(600 * n), proteinTarget: round(30 * n) }
      const servings: ServingRow[] = items.map(item => ({ memberId: item.member.id, memberName: item.member.name, meal: item.meal, bento: item.bento, dishes: menu.map(recipe => ({ recipeId: recipe.id, name: recipe.name, amounts: recipe.servingPerN.map(amount => ({ ...amount, amount: round(amount.amount * item.member.portion) })) })) }))
      const plan: Plan = { id: crypto.randomUUID(), draftId: draft.id, fingerprint: fingerprint(draft), memberFingerprint: memberFingerprint(draft, members), householdId: draft.householdId, date: draft.date, meal: draft.meal, source, totalN: n, recipes: structuredClone(menu), requested: [...draft.requested], available: [...draft.available], budget: draft.budget!, purchases, mealCost, purchaseCost: round(purchases.reduce((sum, row) => sum + (row.purchaseCost ?? 0), 0)), priceComplete: true, nutrition, servings, createdAt: new Date().toISOString(), scope: 'demo' }
      if(eligibility(plan).length) continue
      if(scenario === 'missing-price' && plan.purchases.length) {
        plan.purchases[0] = { ...plan.purchases[0], packagePrice: null, mealCost: null, purchaseCost: null }
        plan.priceComplete = false
        plan.mealCost = round(plan.purchases.reduce((sum, row) => sum + (row.mealCost ?? 0), 0))
        plan.purchaseCost = round(plan.purchases.reduce((sum, row) => sum + (row.purchaseCost ?? 0), 0))
      }
      if(scenario === 'missing-nutrition') plan.nutrition.protein = null
      if(scenario === 'nutrition-range') plan.nutrition.protein = round(plan.nutrition.proteinTarget * 1.5)
      return plan
    }
  }
  throw new PlannerError('no-solution', '目前預算、菜數與營養條件下，沒有符合需求的組合。請修改條件後再試。')
}
export function adoptionErrors(plan: Plan, draft: Draft, members: Member[], records: AdoptedMeal[], acceptedMissingPrice: boolean): string[] {
  const errors = eligibility(plan)
  if(fingerprint(draft) !== plan.fingerprint) errors.push('條件已變更，請重新產生菜單。')
  if(memberFingerprint(draft, members) !== plan.memberFingerprint) errors.push('成員資料已變更，請重新產生菜單。')
  if(!plan.priceComplete && !acceptedMissingPrice) errors.push('請先確認接受部分價格缺漏。')
  const forbidden = new Set(participantItems(draft, members).flatMap(item => [...item.member.allergies, ...item.member.forbidden]))
  if(plan.recipes.some(recipe => recipe.ingredients.some(item => forbidden.has(item.ingredientId)))) errors.push('成員的飲食限制已變更，請重新產生菜單。')
  const excluded = exclusions(draft, records)
  if(plan.recipes.some(recipe => excluded.has(recipe.id))) errors.push('前一天的餐食紀錄已變更，請重新產生菜單。')
  if(draft.requested.some(id => !plan.recipes.some(recipe => recipe.id === id))) errors.push('菜單缺少指定菜色。')
  return errors
}
