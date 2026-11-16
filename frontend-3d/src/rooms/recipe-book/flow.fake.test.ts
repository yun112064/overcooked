/**
 * 用假後端（dev/fake，規則照 backend/app/api）把食譜書的三步走一遍，
 * 確認這裡組出來的請求內容後端收得下：建立 → 存成員 → 讀食譜庫 → 確定 → 菜單 → 營養 → 小知識 → 採用。
 */
import { beforeEach, describe, expect, it } from 'vitest'
import type {
  KnowledgeTips, MealPlanSummary, MealRequest, Member, NutritionPreview, RecipeCatalog, RecipeRetrieval, StapleOptions,
} from '../../api/types'
import type { FakeResponse, FakeState } from '../../../dev/fake/core.ts'
import { handle } from '../../../dev/fake/router.ts'
import { createState } from '../../../dev/fake/seed.ts'
import {
  NO_FILTERS, canToggle, chosenNames, filterDishes, hasStapleDish, initialPicks, initialStep, pageCount, pageOf, pickerDraft,
  stapleFor,
} from './bookLogic'

let state: FakeState

function call(method: string, path: string, body: unknown = {}, headers: Record<string, string> = {}): FakeResponse {
  return handle(state, { method, path, query: {}, headers, body, origin: 'http://localhost:5174' })
}
function ok<T>(res: FakeResponse, status = 200): T {
  expect(res.status, JSON.stringify(res.body)).toBe(status)
  return res.body as T
}
const errorCode = (res: FakeResponse) => (res.body as { error: { code: string } }).error.code

beforeEach(() => {
  state = createState({ now: () => new Date(2026, 10, 14, 12, 0, 0).getTime() })
  ok(call('POST', '/auth/login', { email: 'demo@example.com', password: 'password123' }))
  const { households } = ok<{ households: { id: string; name: string }[] }>(call('GET', '/household'))
  ok(call('POST', '/household/select', { household_id: households.find((h) => h.name === '林家廚房')!.id }))
})

describe('食譜書的流程（對假後端）', () => {
  it('新的一張單：從勾成員到採用', () => {
    const members = ok<{ members: Member[] }>(call('GET', '/members')).members
    const staples = ok<StapleOptions>(call('GET', '/meal-requests/staple-options'))

    // 第一步：阿明、小惠一起吃，小惠另外帶便當
    const picks = initialPicks(members, [])
    const [ming, hui] = members
    picks[ming.id] = { dining: true, bento: false }
    picks[hui.id] = { dining: true, bento: true }
    const created = ok<MealRequest>(call('POST', '/meal-requests', pickerDraft('dinner', picks, undefined, '2026-11-20')), 201)
    expect(created.draft.mode).toBe('picker')
    expect(created.draft.participants).toHaveLength(2)
    expect(created.status).toBe('draft')
    // 換到 /pick/:id 時帶 step: 1，重新整理（沒有 state）則從頭
    expect(initialStep(created, { step: 1 })).toBe(1)
    expect(initialStep(created, null)).toBe(0)

    // 第二步：食譜庫 47 道，一頁 10 道共 5 頁
    const catalog = ok<RecipeCatalog>(call('GET', `/meal-requests/${created.id}/recipe-catalog`))
    const all = filterDishes(catalog.dishes, NO_FILTERS, '')
    expect(pageCount(all.length)).toBe(Math.ceil(catalog.dishes.length / 10))
    expect(pageOf(all, 0)).toHaveLength(10)
    // 小惠花生過敏、阿明不吃茄子：含這些食材的菜標了 conflicts，不能選
    const blocked = catalog.dishes.filter((d) => d.conflicts.length > 0)
    expect(blocked.length).toBeGreaterThan(0)
    expect(blocked.every((d) => !canToggle(d, false, 0))).toBe(true)

    // 挑兩道不是主食、沒有禁忌的菜 → 配清單第一個主食
    const byName = new Map(catalog.dishes.map((d) => [d.dish_name, d]))
    const wanted = catalog.dishes.filter((d) => d.conflicts.length === 0 && d.category !== '主食' && d.meal_type !== '早餐')
      .slice(0, 2).map((d) => d.dish_name)
    const chosen = chosenNames([...wanted, '食譜庫沒有的菜'], byName)
    expect(chosen).toEqual(wanted)
    const staple = stapleFor(hasStapleDish(chosen, byName), staples)
    expect(staple).toBe(staples.options[0].label)
    const saved = ok<MealRequest>(call('PUT', `/meal-requests/${created.id}`, {
      version: created.version,
      draft: { ...pickerDraft('dinner', picks, created.draft), specified_dishes: chosen, dish_count: chosen.length, staple },
    }))
    expect(saved.version).toBe(created.version + 1)
    expect(saved.complete, JSON.stringify(saved.issues)).toBe(true)
    expect(saved.draft.meal_date).toBe('2026-11-20')

    // 舊的版本號再存一次：後端拒絕
    const stale = call('PUT', `/meal-requests/${created.id}`, {
      version: created.version, draft: { ...pickerDraft('lunch', picks, saved.draft) },
    })
    expect(stale.status).toBe(409)
    expect(errorCode(stale)).toBe('VERSION_CONFLICT')

    // 第三步：資料庫菜單就是挑的那幾道 → 營養 → 小知識 → 採用
    const menu = ok<RecipeRetrieval>(call('GET', `/meal-requests/${saved.id}/recipes`))
    expect(menu.selected.map((r) => r.recipe_name)).toEqual(chosen)
    const recipeIds = menu.selected.map((r) => r.recipe_id)
    const nutrition = ok<NutritionPreview>(call('POST', `/meal-requests/${saved.id}/nutrition-preview`, { recipe_ids: recipeIds }))
    expect(['nutrition_passed', 'outside_tolerance', 'calculation_incomplete']).toContain(nutrition.status)
    if (nutrition.status === 'nutrition_passed') {
      const tips = ok<KnowledgeTips>(call('POST', `/meal-requests/${saved.id}/knowledge-tips`, { recipe_ids: recipeIds }))
      expect(['available', 'unavailable']).toContain(tips.status)
    }
    const adopt = call('POST', '/meal-plans',
      { meal_request_id: saved.id, request_version: saved.version, recipe_ids: recipeIds },
      { 'idempotency-key': 'recipe-book-flow-0001' })
    if (nutrition.status === 'nutrition_passed' && !nutrition.adoption_blocked) {
      const plan = ok<{ meal_plan: MealPlanSummary }>(adopt, 201).meal_plan
      expect(plan.meal_request_id).toBe(saved.id)
      // 採用之後這張單就不能再改
      const after = ok<MealRequest>(call('GET', `/meal-requests/${saved.id}`))
      expect(after.status).toBe('confirmed')
      expect(initialStep(after, null)).toBe(2)
      expect(ok<RecipeRetrieval>(call('GET', `/meal-requests/${saved.id}/recipes`)).adopted_plan?.id).toBe(plan.id)
    } else {
      expect(adopt.status).toBeGreaterThanOrEqual(400)
    }
  })

  it('挑了主食類的菜：主食填「不需要主食」，後端不會說重複', () => {
    const members = ok<{ members: Member[] }>(call('GET', '/members')).members
    const staples = ok<StapleOptions>(call('GET', '/meal-requests/staple-options'))
    const picks = initialPicks(members, [])
    picks[members[0].id] = { dining: true, bento: false }
    const created = ok<MealRequest>(call('POST', '/meal-requests', pickerDraft('lunch', picks, undefined, '2026-11-21')), 201)
    const catalog = ok<RecipeCatalog>(call('GET', `/meal-requests/${created.id}/recipe-catalog`))
    const byName = new Map(catalog.dishes.map((d) => [d.dish_name, d]))
    const stapleDish = catalog.dishes.find((d) => d.category === '主食' && d.conflicts.length === 0 && d.meal_type !== '早餐')!
    const side = catalog.dishes.find((d) => d.category !== '主食' && d.conflicts.length === 0 && d.meal_type !== '早餐')!
    const chosen = [stapleDish.dish_name, side.dish_name]
    const staple = stapleFor(hasStapleDish(chosen, byName), staples)
    expect(staple).toBe(staples.none_value)
    const saved = ok<MealRequest>(call('PUT', `/meal-requests/${created.id}`, {
      version: created.version,
      draft: { ...pickerDraft('lunch', picks, created.draft), specified_dishes: chosen, dish_count: chosen.length, staple },
    }))
    const menu = ok<RecipeRetrieval>(call('GET', `/meal-requests/${saved.id}/recipes`))
    expect(menu.reason).not.toBe('SPECIFIED_DISH_STAPLE_CONFLICT')
    expect(menu.selected.map((r) => r.recipe_name).sort()).toEqual([...chosen].sort())
  })

  it('種子裡的食譜庫選菜草稿：已挑的菜帶得進來', () => {
    const list = ok<{ meal_requests: { id: string; mode: string; status: string }[] }>(call('GET', '/meal-requests')).meal_requests
    const draft = list.find((r) => r.mode === 'picker' && r.status === 'draft')!
    const row = ok<MealRequest>(call('GET', `/meal-requests/${draft.id}`))
    const members = ok<{ members: Member[] }>(call('GET', '/members')).members
    const picks = initialPicks(members, row.draft.participants)
    expect(Object.values(picks).some((p) => p.dining || p.bento)).toBe(true)
    const catalog = ok<RecipeCatalog>(call('GET', `/meal-requests/${row.id}/recipe-catalog`))
    const byName = new Map(catalog.dishes.map((d) => [d.dish_name, d]))
    expect(chosenNames(row.draft.specified_dishes, byName)).toEqual(row.draft.specified_dishes)
    // 內容沒變再存一次：照舊前端的做法送整份，後端不升版也不報錯
    const again = ok<MealRequest>(call('PUT', `/meal-requests/${row.id}`, {
      version: row.version, draft: pickerDraft(row.draft.meal_type ?? 'dinner', picks, row.draft),
    }))
    expect(again.version).toBe(row.version)
  })
})
