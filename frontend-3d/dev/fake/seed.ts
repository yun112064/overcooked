import type { Activity, Goal, MealRequestDraft, MealType, Participant } from '../../src/api/types.ts'
import { RECIPES, foodId } from './catalog.ts'
import { addDays, localDate, newId } from './core.ts'
import type { FakeState, HouseholdRow, MemberRow, PlanRow, RequestRow } from './core.ts'
import { adoptPreview, adoptResult } from './plans.ts'
import { householdMembers } from './requests.ts'
import { addResult, advanceJobs, buildResult, submitGeneration } from './results.ts'

/**
 * 種子資料：全部是合成的，重啟就回到這裡。
 * 盡量走和使用者操作相同的函式（送出 → 工作完成 → 採用），這樣種子資料不會出現真實流程做不出來的狀態。
 */

export const DEMO_TAKEN_EMAIL = 'taken@example.com'
const HOUR = 3_600_000

export function emptyState(now: () => number): FakeState {
  return {
    now, fixedToday: null, seq: 0, users: [], session: null, households: [], members: [], requests: [], jobs: [], results: [],
    decisions: [], rejections: [], plans: [], recognitions: [],
    team: {
      recognition: { status: 'online', reason: null },
      knowledge: { status: 'online', reason: null },
      assistant: { status: 'online', reason: null },
      // OpenAI 版小幫手預設未設定：前端不顯示模型切換
      assistant_openai: { status: 'not_configured', reason: 'NOT_CONFIGURED' },
    },
  }
}

/** 暫時把時鐘撥到某個時間點執行，讓種子資料有合理的先後順序。 */
function at<T>(state: FakeState, time: number, run: () => T): T {
  const real = state.now
  state.fixedToday = localDate(real())
  state.now = () => time
  try {
    return run()
  } finally {
    state.now = real
    state.fixedToday = null
  }
}

function household(state: FakeState, name: string, createdAt: number): HouseholdRow {
  const row: HouseholdRow = { id: newId(state, 'household'), name, createdAt, seeded: true }
  state.households.push(row)
  return row
}

type MemberSeed = [name: string, sex: 'M' | 'F', age: number, weight: string, activity: Activity, goal: Goal, portion: string,
  allergens: string[], avoided: string[]]

function members(state: FakeState, householdId: string, rows: MemberSeed[], createdAt: number): MemberRow[] {
  const year = Number(localDate(state.now()).slice(0, 4))
  return rows.map(([name, sex, age, weight, activity, goal, portion, allergens, avoided], index) => {
    const row: MemberRow = {
      id: newId(state, 'member'), householdId, display_name: name,
      // 1 月 2 日生日：一年裡幾乎每一天，年齡都剛好是設定的歲數
      birth_date: `${year - age}-01-02`,
      equation_sex: sex, weight_kg: weight, activity_category: activity, goal, portion,
      allergen_ingredient_ids: allergens.map(foodId), avoided_ingredient_ids: avoided.map(foodId),
      version: 1, createdAt: createdAt + index, updatedAt: createdAt + index,
    }
    state.members.push(row)
    return row
  })
}

const dining = (m: MemberRow, extra: Partial<Participant> = {}): Participant =>
  ({ member_id: m.id, dining: true, bento: false, bento_meal: 'lunch', ...extra })

function draft(fields: Partial<MealRequestDraft>): MealRequestDraft {
  return {
    mode: 'questionnaire', meal_date: null, meal_type: null, budget: null, staple: null, dish_count: null,
    taste_preferences: '', specified_dishes: [], participants: [], available_ingredient_ids: [], ...fields,
  }
}

function request(state: FakeState, householdId: string, fields: Partial<MealRequestDraft>, createdAt: number): RequestRow {
  const row: RequestRow = {
    id: newId(state, 'request'), householdId, status: 'draft', version: 1, draft: draft(fields), createdAt, updatedAt: createdAt,
  }
  state.requests.push(row)
  return row
}

/** 送出並等到工作結束（成功、失敗都算）。 */
function generate(state: FakeState, row: RequestRow, time: number): void {
  at(state, time, () => submitGeneration(state, row, row.version, `seed-key-${row.id}`))
  at(state, time + 30_000, () => advanceJobs(state))
}

type History = { daysAgo: number; meal: MealType; kind: 'result' | 'preview' | 'legacy'; budget?: string }

/** 過去的已採用紀錄：日期用「幾天前」保存，查詢時才換算，所以永遠是相對於今天。 */
function history(state: FakeState, householdId: string, people: Participant[], entries: History[], pickerPeople: Participant[]): void {
  const todayIso = localDate(state.now())
  const membersMap = householdMembers(state, householdId)
  entries.forEach((entry, index) => {
    const date = addDays(todayIso, -entry.daysAgo)
    const adoptedAt = state.now() - entry.daysAgo * 24 * HOUR - 2 * HOUR
    const base: Omit<PlanRow, 'id' | 'status' | 'dishes' | 'result' | 'preview' | 'mode'> = {
      householdId, date: { daysAgo: entry.daysAgo }, mealType: entry.meal, adoptedAt, requestId: null, resultVersion: null,
      requestVersion: null, priceExceptionAccepted: false, idempotencyKey: `seed-plan-${householdId}-${index}`,
      commandHash: `seed-${index}`,
    }
    if (entry.kind === 'legacy') {
      // 最小紀錄：只有菜名、沒有營養快照，趨勢圖上這一天的熱量是未知（不是 0）
      const picks = [RECIPES[16], RECIPES[27]]
      state.plans.push({
        ...base, id: newId(state, 'plan'), status: 'adopted', mode: 'database_menu', result: null, preview: null,
        dishes: picks.map((r) => ({ standard_dish_id: r.standard_dish_id, dish_name: r.name, aliases: r.aliases, source: 'database', recipe_id: r.recipe_id })),
      })
      return
    }
    if (entry.kind === 'preview') {
      // 食譜庫選菜的採用：走真正的採用函式，再把日期改成相對日期
      const row = request(state, householdId, {
        mode: 'picker', meal_date: date, meal_type: entry.meal, staple: '白飯', dish_count: 3,
        specified_dishes: ['番茄炒蛋', '蒜炒高麗菜', '香菇雞湯'], participants: pickerPeople,
      }, adoptedAt - HOUR)
      const { plan } = at(state, adoptedAt, () => adoptPreview(state, householdId,
        { meal_request_id: row.id, request_version: 1, recipe_ids: ['番茄炒蛋', '蒜炒高麗菜', '香菇雞湯'].map((n) => RECIPES.find((r) => r.name === n)!.recipe_id) },
        `seed-picker-${row.id}`))
      plan.date = { daysAgo: entry.daysAgo }
      return
    }
    const input = {
      householdId, members: membersMap, at: adoptedAt,
      draft: draft({ meal_date: date, meal_type: entry.meal, budget: entry.budget ?? '500', staple: '白飯', dish_count: 3, participants: people }),
    }
    // 換幾組菜，直到找到營養通過、成本不超預算的那一組（歷史紀錄一定是可採用的）
    for (let rotation = index; rotation < index + 8; rotation += 1) {
      const built = buildResult(state, { ...input, rotation })
      if (!built.ok || built.body.blockCodes.length || built.body.prices.budget_status === 'BUDGET_EXCEEDED') continue
      const result = addResult(state, null, householdId, 1, adoptedAt, built, null)
      state.plans.push({
        ...base, id: newId(state, 'plan'), status: 'adopted', mode: result.mode, resultVersion: 1, requestVersion: 1,
        priceExceptionAccepted: built.body.prices.budget_status === 'PRICE_INCOMPLETE',
        dishes: built.body.dishes.map((d) => ({ standard_dish_id: d.standard_dish_id, dish_name: d.dish_name, aliases: d.aliases, source: d.source, recipe_id: d.recipe_id })),
        result: { body: built.body, fallbackReason: result.fallbackReason, historyChanged: false }, preview: null,
      })
      return
    }
    throw new Error(`種子資料：${date} 找不到可採用的菜單`)
  })
}

export function seed(state: FakeState): void {
  const now = state.now()
  const todayIso = localDate(now)
  const tomorrow = addDays(todayIso, 1)
  const dayAfter = addDays(todayIso, 2)

  // ---- 林家廚房：4 位成員，阿嬤的設定算不出每日目標 ----
  const lin = household(state, '林家廚房', now - 60 * 24 * HOUR)
  const [ming, hui, an, grandma] = members(state, lin.id, [
    ['阿明', 'M', 42, '72', 'low_active', 'maintain', '1.15', [], ['茄子']],
    ['小惠', 'F', 40, '55', 'active', 'general', '1', ['花生'], []],
    ['小安', 'M', 5, '19', 'low_active', 'general', '0.85', ['白蝦'], ['青椒']],
    // 體重輕又設減脂：每日熱量會低於 1200 大卡，營養目標算不出來（要到成員頁改目標才會好）
    ['阿嬤', 'F', 78, '38', 'sedentary', 'fat_loss', '0.7', [], []],
  ], now - 60 * 24 * HOUR)
  const family = [dining(ming), dining(hui), dining(an)]

  history(state, lin.id, family, [
    { daysAgo: 25, meal: 'dinner', kind: 'result' },
    { daysAgo: 22, meal: 'dinner', kind: 'result', budget: '450' },
    { daysAgo: 20, meal: 'lunch', kind: 'result' },
    { daysAgo: 18, meal: 'dinner', kind: 'result', budget: '600' },
    { daysAgo: 16, meal: 'dinner', kind: 'result' },
    { daysAgo: 14, meal: 'dinner', kind: 'result', budget: '400' },
    { daysAgo: 13, meal: 'lunch', kind: 'result' },
    { daysAgo: 13, meal: 'dinner', kind: 'result', budget: '550' },
    { daysAgo: 11, meal: 'dinner', kind: 'result' },
    { daysAgo: 9, meal: 'dinner', kind: 'result', budget: '450' },
    { daysAgo: 8, meal: 'dinner', kind: 'result' },
    { daysAgo: 6, meal: 'dinner', kind: 'result', budget: '600' },
    // 第 5 天前刻意沒有紀錄；第 4 天前是營養未知的最小紀錄
    { daysAgo: 4, meal: 'dinner', kind: 'legacy' },
    { daysAgo: 3, meal: 'dinner', kind: 'result' },
    { daysAgo: 2, meal: 'lunch', kind: 'preview' },
    { daysAgo: 2, meal: 'dinner', kind: 'result', budget: '450' },
    { daysAgo: 1, meal: 'dinner', kind: 'result' },
  ], [dining(hui), dining(an)])

  // 已採用：今天午餐
  const adopted = request(state, lin.id, {
    meal_date: todayIso, meal_type: 'lunch', budget: '500', staple: '白飯', dish_count: 3,
    specified_dishes: ['香煎鮭魚', '清炒青江菜', '番茄蛋花湯'], participants: family,
  }, now - 5 * HOUR)
  generate(state, adopted, now - 4 * HOUR)
  at(state, now - 3 * HOUR, () => adoptResult(state, lin.id,
    { meal_request_id: adopted.id, result_version: 1, request_version: 1 }, `seed-adopt-${adopted.id}`))

  // 已有結果、可以採用：今天晚餐。主食、海鮮、蔬菜、湯各一道；鱸魚查不到價格、海帶芽的膳食纖維未知
  const ready = request(state, lin.id, {
    meal_date: todayIso, meal_type: 'dinner', budget: '600', staple: '不需要主食', dish_count: 4,
    specified_dishes: ['地瓜糙米飯', '清蒸鱸魚', '蒜炒高麗菜', '味噌豆腐湯'], participants: family,
    available_ingredient_ids: [foodId('蒜頭'), foodId('食鹽')],
  }, now - 2 * HOUR)
  generate(state, ready, now - 90 * 60_000)

  // 等你決定：缺必要營養（熱量未知），可以選「繼續查看」但不能採用
  const waiting = request(state, lin.id, {
    meal_date: tomorrow, meal_type: 'dinner', budget: '550', staple: '糙米飯', dish_count: 3,
    taste_preferences: '少油一點 nonutri', participants: [dining(ming), dining(hui, { bento: true })],
  }, now - 80 * 60_000)
  generate(state, waiting, now - 70 * 60_000)

  // 生成失敗
  const failed = request(state, lin.id, {
    meal_date: dayAfter, meal_type: 'lunch', budget: '400', staple: '白飯', dish_count: 2,
    taste_preferences: '想吃辣 fail', participants: [dining(ming), dining(hui)],
  }, now - 50 * 60_000)
  generate(state, failed, now - 45 * 60_000)

  // 食譜庫選菜（picker）：已挑好菜，可以直接看資料庫菜單並採用
  request(state, lin.id, {
    mode: 'picker', meal_date: dayAfter, meal_type: 'dinner', staple: '白飯', dish_count: 3,
    specified_dishes: ['蔥油雞腿', '蒜香菠菜', '玉米蘿蔔湯'], participants: [dining(hui), dining(an)],
  }, now - 30 * 60_000)

  // 草稿：還沒填完，而且選了阿嬤，所以營養目標算不出來
  request(state, lin.id, {
    meal_date: todayIso, meal_type: 'dinner', participants: [dining(ming), dining(grandma)],
  }, now - 20 * 60_000)

  // 生成中：啟動後排隊 5 秒，接著執行很久（暗號 slow），可以慢慢看進度或按取消
  const running = request(state, lin.id, {
    meal_date: tomorrow, meal_type: 'lunch', budget: '450', staple: '白飯', dish_count: 3,
    taste_preferences: '清淡 slow', participants: family,
  }, now - 60_000)
  submitGeneration(state, running, 1, `seed-key-${running.id}`)

  // ---- 外婆家：2 位成員 ----
  const grandmaHome = household(state, '外婆家', now - 30 * 24 * HOUR)
  const [po, gong] = members(state, grandmaHome.id, [
    ['外婆', 'F', 70, '52', 'sedentary', 'general', '0.85', [], []],
    ['外公', 'M', 73, '65', 'low_active', 'maintain', '1', [], ['羊肉片']],
  ], now - 30 * 24 * HOUR)
  const elders = [dining(po), dining(gong)]
  history(state, grandmaHome.id, elders, [
    { daysAgo: 5, meal: 'dinner', kind: 'result', budget: '350' },
    { daysAgo: 3, meal: 'lunch', kind: 'result', budget: '350' },
    { daysAgo: 1, meal: 'dinner', kind: 'result', budget: '350' },
  ], elders)
  // 填好的草稿：可以直接送出生成
  request(state, grandmaHome.id, {
    meal_date: tomorrow, meal_type: 'lunch', budget: '350', staple: '白飯', dish_count: 2, participants: elders,
  }, now - 10 * 60_000)

  // 這個 Email 已經註冊過：用來看「此 Email 已註冊」的錯誤
  state.users.push({ id: newId(state, 'user'), email: DEMO_TAKEN_EMAIL, householdIds: [lin.id, grandmaHome.id] })
}

export function createState(options: { now?: () => number; seed?: boolean } = {}): FakeState {
  const state = emptyState(options.now ?? Date.now)
  if (options.seed !== false) seed(state)
  return state
}
