import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ingredients, recipes, staples } from '../data/catalog.ts'
import { adoptionErrors, createDraft, eligibility, exclusions, generateDemoPlan, previousDate, purchaseRows, totalPortions, validateDraft } from './rules.ts'
import { memberSchema } from './schemas.ts'
import { seedData } from '../services/seed.ts'
import { demoPlanner } from '../services/planner.ts'
import type { AdoptedMeal } from './types.ts'

function fixture() {
  const data = seedData(), draft = createDraft(data.activeHouseholdId, data.members, '2030-02-15')
  Object.assign(draft, { budget: 500, staple: 'rice', requested: ['eggs', 'cabbage', 'mushroom-soup'] })
  const plan = generateDemoPlan(draft, data.members, [], recipes, staples.rice, ingredients)
  return { data, draft, plan }
}
test('previous calendar day handles leap years and year boundaries', () => {
  assert.equal(previousDate('2024-03-01'), '2024-02-29')
  assert.equal(previousDate('2026-01-01'), '2025-12-31')
})
test('dining and bento are independent participation items', () => {
  const { draft, data } = fixture()
  draft.participation[1].bento = true
  assert.equal(totalPortions(draft, data.members), 4)
  draft.participation.forEach(item => { item.dining = false })
  assert.equal(totalPortions(draft, data.members), 1.15)
  assert.equal(validateDraft(draft, data.members).length, 0)
})
test('members outside the selected household cannot participate', () => {
  const { draft, data } = fixture()
  draft.householdId = 'another-household'
  assert.equal(totalPortions(draft, data.members), 0)
  assert.ok(validateDraft(draft, data.members).some(error => error.includes('至少選擇')))
})
test('whole-package expenditure differs from meal cost', () => {
  const catalog = [{ id: 'sample', name: '範例', unit: 'g' as const, packageAmount: 300, packagePrice: 60 }]
  const recipe = { ...recipes[0], ingredients: [{ ingredientId: 'sample', amount: 100 }] }
  const [price] = purchaseRows([recipe], 1, [], catalog)
  assert.equal(price.purchaseCost, 60)
  assert.equal(price.mealCost, 20)
  assert.equal(price.packages, 1)
  assert.deepEqual(purchaseRows([recipe], 1, ['sample'], catalog), [])
})
test('available ingredients have no procurement rows but keep recipe nutrition', () => {
  const { draft, data, plan } = fixture()
  draft.available = ingredients.map(item => item.id)
  const allAvailable = generateDemoPlan(draft, data.members, [], recipes, staples.rice, ingredients)
  assert.equal(allAvailable.mealCost, 0)
  assert.equal(allAvailable.purchaseCost, 0)
  assert.equal(allAvailable.nutrition.energy, plan.nutrition.energy)
})
test('requested dishes conflict with allergies rather than being replaced', () => {
  const { draft, data } = fixture()
  data.members[0].allergies.push('egg')
  assert.throws(() => generateDemoPlan(draft, data.members, [], recipes, staples.rice, ingredients), /衝突/)
})
test('yesterday exclusion uses every meal and permits explicitly requested dishes', () => {
  const { draft, plan } = fixture()
  const records: AdoptedMeal[] = ['breakfast', 'lunch'].map((meal, index) => ({ id: String(index), adoptedAt: '', acceptedMissingPrice: false, plan: { ...plan, date: '2030-02-14', meal: meal as 'breakfast' | 'lunch', recipes: [recipes[index]] } }))
  draft.requested = ['eggs']
  const excluded = exclusions(draft, records)
  assert.equal(excluded.has('eggs'), false)
  assert.equal(excluded.has('cabbage'), true)
  assert.equal(exclusions({ ...draft, householdId: 'other' }, records).size, 0)
  assert.equal(exclusions({ ...draft, date: '2030-02-16' }, records).size, 0)
})
test('exhausted database choices use explicitly marked demo AI recipes', () => {
  const { draft, data, plan } = fixture()
  draft.requested = []
  const old: AdoptedMeal = { id: 'old', acceptedMissingPrice: false, adoptedAt: '', plan: { ...plan, date: previousDate(draft.date), recipes: recipes.filter(recipe => recipe.source === 'database_menu') } }
  const next = generateDemoPlan(draft, data.members, [old], recipes, staples.rice, ingredients)
  assert.equal(next.source, 'ai_generated_menu')
  assert.ok(next.recipes.filter(recipe => recipe.kind === 'dish').every(recipe => recipe.source === 'ai_generated_menu'))
})
test('unknown nutrition blocks adoption; null and actual zero stay distinct', () => {
  const { plan } = fixture()
  assert.equal(eligibility({ ...plan, nutrition: { ...plan.nutrition, protein: null } }).some(error => error.includes('缺少')), true)
  assert.equal(eligibility({ ...plan, nutrition: { ...plan.nutrition, protein: 0 } }).some(error => error.includes('蛋白質估算')), true)
})
test('nutrition limits include both exact 10% boundaries', () => {
  const { plan } = fixture()
  assert.deepEqual(eligibility({ ...plan, nutrition: { energy: 540, energyTarget: 600, protein: 33, proteinTarget: 30 } }), [])
  assert.ok(eligibility({ ...plan, nutrition: { energy: 539.99, energyTarget: 600, protein: 30, proteinTarget: 30 } }).length)
})
test('missing-price adoption requires consent and never permits known over-budget costs', () => {
  const { draft, data, plan } = fixture()
  const missing = { ...plan, priceComplete: false }
  assert.ok(adoptionErrors(missing, draft, data.members, [], false).some(error => error.includes('缺漏')))
  assert.deepEqual(adoptionErrors(missing, draft, data.members, [], true), [])
  assert.ok(adoptionErrors({ ...missing, mealCost: 501 }, draft, data.members, [], true).some(error => error.includes('超出預算')))
})
test('changing a draft or member profile invalidates adoption', () => {
  const { draft, data, plan } = fixture()
  assert.ok(adoptionErrors(plan, { ...draft, revision: 2 }, data.members, [], true).some(error => error.includes('條件已變更')))
  data.members[0].weight += 1
  assert.ok(adoptionErrors(plan, draft, data.members, [], true).some(error => error.includes('成員資料已變更')))
})
test('adoption stores an independent snapshot and is idempotent', () => {
  const { draft, data, plan } = fixture()
  const adopted = demoPlanner.adopt(plan, draft, data.members, [], false)
  assert.equal(demoPlanner.adopt(plan, draft, data.members, [adopted], false), adopted)
  assert.throws(() => demoPlanner.adopt(plan, { ...draft, budget: 800 }, data.members, [adopted], false), /條件已變更/)
  const saved = adopted.plan.recipes[0].name
  plan.recipes[0].name = '修改後的名稱'
  assert.equal(adopted.plan.recipes[0].name, saved)
})
test('under-19 member goals and the five supported portions are validated', () => {
  const { data } = fixture()
  assert.equal(memberSchema.safeParse({ ...data.members[0], age: 18, goal: 'muscle_gain' }).success, false)
  assert.equal(memberSchema.safeParse({ ...data.members[0], age: 18, goal: 'general' }).success, true)
  assert.equal(memberSchema.safeParse({ ...data.members[0], portion: 1.2 }).success, false)
})
test('generation can be cancelled without returning a late result', async () => {
  const { draft, data } = fixture(), controller = new AbortController()
  const pending = demoPlanner.generate(draft, data.members, [], 'normal', controller.signal)
  controller.abort()
  await assert.rejects(pending, { name: 'AbortError' })
})
