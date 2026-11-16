/**
 * 把主要的一趟路用假後端（dev/fake，規則照 backend/app/api）走一遍，確認「各個房間自己的判斷」接得起來：
 *
 *   大門 → 玄關 → 客廳 → 廚房 → 新的一餐 → 問卷五步 → 送出 → 生成中 → 餐桌 → 決定 → 採用
 *   → 客廳看得到 → 冰箱門看得到 → 全家福新增／修改成員 → 飲食小幫手 → 換家庭、登出
 *
 * 每一步送的內容用的是場景自己的函式（toDraft、adoptBody、toBody…），讀回來的資料也交給場景自己的
 * 判斷（stovePots、destinationFor、tableActions、planForMeal、byDay…）。這樣某個房間改了規則、
 * 另一個房間沒跟上時，這裡會先壞。畫面長什麼樣不在這裡檢查。
 */
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it } from 'vitest'
import type { FakeResponse, FakeState } from '../dev/fake/core.ts'
import { handle } from '../dev/fake/router.ts'
import { createState } from '../dev/fake/seed.ts'
import type { AssistantReference, AssistantType } from './api/assistant'
import type {
  Job, Me, MealRequest, MealRequestSummary, MealResult, MealResultSummary, Member, MemberOptions, NutritionPreview,
  RecipeCatalog, RecipeRetrieval, StapleOptions,
} from './api/types'
import { answerBadge, buildHistory, citedReferences } from './assistant/chatLogic'
import type { Message } from './assistant/chatStore'
import { latestGenerationJob, potState, stovePots } from './pot/potState'
import { adoptBody } from './rooms/dining/resultText'
import { buildSeats, buildVessels, resultFlags, statusSummary, tableActions } from './rooms/dining/tableModel'
import {
  byDay, canCheckout, isGenerationPlan, normalizeTrend, planDishNames, planEnergy, requestPath, tablePath,
  type AdoptedPlan, type TrendItem as FridgeTrendItem,
} from './rooms/fridge/records'
import { destinationFor, latestAdoptedDate, resultPath, weekStats, type TrendItem as KitchenTrendItem } from './rooms/kitchen/kitchenLogic'
import { dayBars, dishNames, planForMeal, planRatios, type TodayPlan, type TrendItem as LivingTrendItem } from './rooms/living/today'
import { potSteps, requestedStep, stapleChoice, toDraft, toForm } from './rooms/passboard/draft'
import { isActive, latestResultVersion, stageList, submitState } from './rooms/passboard/generation'
import { buildSchema, toBody, toForm as memberForm } from './rooms/portrait/memberRules'
import { chosenNames, hasStapleDish, initialPicks, initialStep as bookStep, pickerDraft, stapleFor } from './rooms/recipe-book/bookLogic'
import {
  generationJobsQuery, invalidateAdoptedPlans, localDate, mealRequestsQuery, planDatesQuery, plansOnDateQuery, trendQuery,
  type TrendResponse,
} from './session/queries'
import { afterHouseholdChange, afterLogin, afterLogout } from './session/session'

// 假時鐘：測試自己決定現在幾點，生成工作就能快轉
const START = new Date(2026, 10, 14, 12, 0, 0).getTime()
let clock = START
let state: FakeState
let keySeq = 0
const tick = (seconds: number) => { clock += seconds * 1000 }
/** 冪等鍵。axios 送出的標頭名稱後端不分大小寫；假後端的 handle() 收小寫。 */
const key = () => ({ 'idempotency-key': `journey-key-${String(++keySeq).padStart(4, '0')}` })

function call(method: string, path: string, body: unknown = {}, headers: Record<string, string> = {}, query: Record<string, string> = {}): FakeResponse {
  return handle(state, { method, path, query, headers, body, origin: 'http://localhost:5174' })
}
function ok<T>(res: FakeResponse, status = 200): T {
  expect(res.status, JSON.stringify(res.body)).toBe(status)
  return res.body as T
}
const get = <T>(path: string, query: Record<string, string> = {}) => ok<T>(call('GET', path, {}, {}, query))
const errorOf = (res: FakeResponse) => (res.body as { error: { code: string; details?: { reason?: string } } }).error

// ---- 各房間讀資料的方式（端點與參數和 session/queries.ts 的查詢相同） ----
const today = () => localDate(new Date(clock))
const requests = () => get<{ meal_requests: MealRequestSummary[] }>('/meal-requests').meal_requests
const houseJobs = () => get<{ jobs: Job[] }>('/jobs', { kind: 'meal_generation' }).jobs
const requestJob = (id: string) => get<{ jobs: Job[] }>('/jobs', { meal_request_id: id, kind: 'meal_generation' }).jobs[0] ?? null
const results = (id: string) => get<{ results: MealResultSummary[] }>(`/meal-requests/${id}/results`).results
const result = (id: string, version: number) => get<MealResult>(`/meal-requests/${id}/results/${version}`)
const plansOn = <P>(date: string) => get<{ meal_plans: P[] }>('/meal-plans', { date }).meal_plans
const trend = <I>(days: number) => get<TrendResponse<I>>('/meal-plans/trend', { days: String(days), until: today() })
const planDates = () => get<{ dates: { meal_date: string; count: number }[] }>('/meal-plans').dates

function enter(household = '林家廚房'): Me {
  ok(call('POST', '/auth/login', { email: 'demo@example.com', password: 'password123' }))
  const { households } = get<{ households: { id: string; name: string }[] }>('/household')
  ok(call('POST', '/household/select', { household_id: households.find((h) => h.name === household)!.id }))
  return get<Me>('/auth/me')
}

beforeEach(() => {
  clock = START
  keySeq = 0
  state = createState({ now: () => clock })
})

describe('進門', () => {
  it('沒登入是 401（大門）；登入後還沒選家庭（玄關）；選好才進得了屋', () => {
    expect(call('GET', '/auth/me').status).toBe(401)
    const me = ok<Me>(call('POST', '/auth/login', { email: 'demo@example.com', password: 'password123' }))
    // 兩個家庭：不會自動選，DoorScene 依 current_household 決定去玄關
    expect(me.current_household).toBeNull()
    const blocked = call('GET', '/meal-requests')
    expect(blocked.status).toBe(409)
    expect(errorOf(blocked).code).toBe('HOUSEHOLD_NOT_SELECTED')
    expect(enter().current_household?.name).toBe('林家廚房')
  })
})

describe('客廳與廚房看到的是同一口鍋', () => {
  beforeEach(() => { enter() })

  it('種子資料：兩個房間用同一份清單與同一個判斷；每張單要去的網址都是存在的路由', () => {
    const list = requests()
    const jobs = houseJobs()
    const stove = stovePots(list, jobs)
    // 左爐口是最新一份還沒採用、而且不是還在挑菜的單
    expect(stove.left.request).not.toBeNull()
    expect(stove.left.request?.status).not.toBe('confirmed')
    expect(stove.left.request?.mode === 'picker' && !['cooking', 'ready', 'failed'].includes(stove.left.state.kind)).toBe(false)

    const routes = [/^\/plan\/[^/]+$/, /^\/plan\/[^/]+\/cooking$/, /^\/plan\/[^/]+\/result\/\d+$/, /^\/pick\/[^/]+$/]
    for (const request of list) {
      const pot = potState({ request, job: latestGenerationJob(jobs, request.id) })
      const destination = destinationFor(request, pot)
      const path = destination.kind === 'path' ? destination.to : resultPath(destination.requestId, results(destination.requestId))
      expect(routes.some((route) => route.test(path)), `${request.status} → ${path}`).toBe(true)
      // 有結果或已採用的問卷一定找得到那一桌（不會掉回問卷）
      if (destination.kind === 'result') expect(path).toMatch(/\/result\/\d+$/)
      // 冰箱門的生成紀錄用舊清單的規則：選菜的去食譜書，其餘去單板
      expect(requestPath(request)).toBe(request.mode === 'picker' ? `/pick/${request.id}` : `/plan/${request.id}`)
    }
  })

  it('客廳：今天午餐是已採用的那一餐；7 天長條裡未知的那一天不是 0', () => {
    const plans = plansOn<TodayPlan>(today())
    const lunch = planForMeal(plans, 'lunch')
    expect(lunch.plan).not.toBeNull()
    expect(dishNames(lunch.plan!).length).toBeGreaterThan(0)
    expect(planRatios(lunch.plan!).energy).not.toBeNull()
    expect(planForMeal(plans, 'breakfast').plan).toBeNull()

    const bars = [...dayBars(trend<LivingTrendItem>(7).items, 'lunch', today()), ...dayBars(trend<LivingTrendItem>(7).items, 'dinner', today())]
    expect(bars.some((bar) => bar.state === 'unknown')).toBe(true)
    for (const bar of bars.filter((b) => b.state === 'unknown')) expect(bar.ratio).toBeNull()
    expect(bars.some((bar) => bar.state === 'none')).toBe(true)
  })
})

describe('主要的一趟路：新的一餐 → 五步 → 送出 → 餐桌 → 採用 → 客廳與冰箱門', () => {
  beforeEach(() => { enter() })

  it('走完整趟', () => {
    const members = get<{ members: Member[] }>('/members').members
    const staples = get<StapleOptions>('/meal-requests/staple-options')
    const date = today()

    // 使用者在客廳待了一分鐘才走進廚房（種子裡那份排隊中的單已經開始煮）
    tick(60)

    // ---- 廚房的空鍋／冰箱把手：開一張今天的單 ----
    let row = ok<MealRequest>(call('POST', '/meal-requests', { meal_date: date }), 201)
    expect(row.status).toBe('draft')
    expect(row.draft.mode).toBe('questionnaire')
    // 冰箱把手帶 ?step=3：草稿照網址開在第 3 步（拍家裡有的食材）
    expect(requestedStep('3', row.status)).toBe(2)
    // 只有日期的單在爐上還是空鍋；兩個房間都這樣看
    expect(potState({ request: row }).kind).toBe('empty')
    expect(stovePots(requests(), houseJobs()).left.request?.id).toBe(row.id)

    // ---- 問卷：每換一步就存一次（PUT { version, draft }），鍋裡多一樣料 ----
    const form = toForm(row, members)
    const save = (): MealRequest => {
      const saved = ok<MealRequest>(call('PUT', `/meal-requests/${row.id}`, { version: row.version, draft: toDraft(form) }))
      expect(saved.version).toBe(row.version + 1)
      return saved
    }
    // 第 1 步：餐期、餐別、預算
    form.meal_type = 'dinner'
    form.budget = '600'
    row = save()
    expect(potSteps(row, 1)).toBe(1)
    expect(potState({ request: row, stepsDone: potSteps(row, 1) })).toEqual({ kind: 'filling', steps: 1 })
    // 清單上的摘要沒有 issues，廚房只能粗略判斷，但一樣是「備料中 1/5」
    const summary = requests().find((r) => r.id === row.id)!
    expect(potState({ request: summary })).toEqual({ kind: 'filling', steps: 1 })

    // 第 2 步：誰要吃（不選阿嬤：她的設定算不出目標）
    for (const member of members.filter((m) => m.display_name !== '阿嬤')) {
      form.participants[member.id] = { dining: true, bento: false, bento_meal: 'lunch' }
    }
    row = save()
    expect(row.draft.participants).toHaveLength(members.length - 1)
    expect(potSteps(row, 2)).toBe(2)
    // 第 3 步可以略過：走過去就算（單板自己知道走到哪了）
    expect(potSteps(row, 3)).toBe(3)

    // 第 4 步：主食、幾道菜
    const staple = stapleChoice(staples.options[0].label, staples)
    expect(staple).toBe(staples.options[0].label)
    form.staple = staple!
    form.dish_count = '3'
    row = save()
    expect(row.complete, JSON.stringify(row.issues)).toBe(true)
    expect(potState({ request: row, stepsDone: potSteps(row, 4) }).kind).toBe('full')
    expect(submitState(row, null)).toMatchObject({ canSubmit: true, showSubmit: true, submitLabel: '開始煮' })

    // 內容沒變再存一次不升版；拿舊版本存是 409（單板會請使用者重新載入）
    expect(ok<MealRequest>(call('PUT', `/meal-requests/${row.id}`, { version: row.version, draft: toDraft(form) })).version).toBe(row.version)
    const stale = call('PUT', `/meal-requests/${row.id}`, { version: row.version - 1, draft: { ...toDraft(form), dish_count: 4 } })
    expect(stale.status).toBe(409)
    expect(errorOf(stale).code).toBe('VERSION_CONFLICT')

    // ---- 第 5 步：開始煮 ----
    const submitKey = key()
    let job = ok<{ job: Job }>(call('POST', `/meal-requests/${row.id}/submit`, { version: row.version }, submitKey), 202).job
    expect(isActive(job)).toBe(true)
    // 同一把鍵重送（連點、網路重送）不會多一個工作
    expect(ok<{ job: Job }>(call('POST', `/meal-requests/${row.id}/submit`, { version: row.version }, submitKey), 202).job.id).toBe(job.id)

    row = get<MealRequest>(`/meal-requests/${row.id}`)
    expect(['queued', 'running']).toContain(row.status)
    // 單板、廚房、客廳：都是蓋著鍋蓋在煮；廚房點它會到 /cooking
    expect(potState({ request: row, job }).kind).toBe('cooking')
    let list = requests()
    let jobs = houseJobs()
    const cookingPot = stovePots(list, jobs)
    const onStove = [cookingPot.left, cookingPot.right].find((pot) => pot?.request?.id === row.id)
    expect(onStove?.state.kind).toBe('cooking')
    expect(destinationFor(list.find((r) => r.id === row.id)!, onStove!.state)).toEqual({ kind: 'path', to: `/plan/${row.id}/cooking` })
    // 生成中不能改問卷
    expect(errorOf(call('PUT', `/meal-requests/${row.id}`, { version: row.version, draft: toDraft(form) })).details?.reason)
      .toBe('GENERATION_IN_PROGRESS')

    // ---- 生成中：階段會往前走，最後成功 ----
    tick(3)
    job = requestJob(row.id)!
    expect(job.status).toBe('running')
    const stages = stageList(job)
    expect(stages.items.filter((item) => item.state === 'current')).toHaveLength(1)
    tick(8)
    job = requestJob(row.id)!
    expect(job.status).toBe('succeeded')
    const version = latestResultVersion(results(row.id), job.result_summary?.result_version ?? job.result_version)
    expect(version).toBe(1)

    row = get<MealRequest>(`/meal-requests/${row.id}`)
    expect(row.status).toBe('result_ready')
    // 鍋開蓋；廚房點它會先查最新版本再上桌
    list = requests()
    jobs = houseJobs()
    const ready = potState({ request: list.find((r) => r.id === row.id)!, job: latestGenerationJob(jobs, row.id) })
    expect(ready.kind).toBe('ready')
    expect(destinationFor(list.find((r) => r.id === row.id)!, ready)).toEqual({ kind: 'result', requestId: row.id })
    expect(resultPath(row.id, results(row.id))).toBe(`/plan/${row.id}/result/1`)

    // ---- 餐桌 ----
    let table = result(row.id, version!)
    const vessels = buildVessels(table)
    expect(vessels[0].kind).toBe('staple')
    expect(vessels).toHaveLength(table.dishes.length + 1)
    const seats = buildSeats(table.nutrition)
    expect(seats.map((seat) => seat.name).sort()).toEqual(members.filter((m) => m.display_name !== '阿嬤').map((m) => m.display_name).sort())
    // 算不出來的是 unknown（空碗），不會出現 0%
    for (const seat of seats) if (seat.verdict === 'unknown') expect(seat.energy === null || seat.protein === null).toBe(true)

    let flags = resultFlags(table)
    expect(statusSummary(table, flags).text).toBe('可以採用')
    const actions = tableActions(table, flags, { cooking: false, requestLoaded: true, adoptedHere: false, canShop: false })
    expect(actions[0]).toEqual({ kind: 'adopt', disabled: false })
    expect(actions.map((a) => a.kind)).toContain('regenerate')

    // 整包花費和按比例成本是兩個數字（後端各給各的，前端不相加）
    expect(table.prices).toHaveProperty('purchase_total')
    expect(table.prices).toHaveProperty('cost_total')

    // ---- 採用（POST /meal-plans；同一把鍵重送只成立一次） ----
    const adoptKey = key()
    const plan = ok<{ meal_plan: { id: string; meal_date: string } }>(call('POST', '/meal-plans', adoptBody(row.id, table), adoptKey), 201).meal_plan
    expect(ok<{ meal_plan: { id: string } }>(call('POST', '/meal-plans', adoptBody(row.id, table), adoptKey)).meal_plan.id).toBe(plan.id)
    expect(plan.meal_date).toBe(date)

    // 採用後：問卷變成已採用，餐桌唯讀；剛採用完的動作列是離開的路
    table = result(row.id, version!)
    flags = resultFlags(table)
    expect(flags.confirmed).toBe(true)
    expect(statusSummary(table, flags, 1).text).toBe('已採用')
    expect(tableActions(table, flags, { cooking: false, requestLoaded: true, adoptedHere: false, canShop: false })).toEqual([])
    expect(tableActions(table, flags, { cooking: false, requestLoaded: true, adoptedHere: true, canShop: false }).map((a) => a.kind))
      .toEqual(['home', 'records'])
    row = get<MealRequest>(`/meal-requests/${row.id}`)
    expect(row.status).toBe('confirmed')
    expect(submitState(row, requestJob(row.id))).toMatchObject({ confirmed: true, canSubmit: false, showSubmit: false, canRegenerate: false })
    expect(errorOf(call('PUT', `/meal-requests/${row.id}`, { version: row.version, draft: { ...toDraft(form), dish_count: 4 } })).details?.reason)
      .toBe('ALREADY_ADOPTED')

    // ---- 廚房：這口鍋下爐了；點單軌上那張單會到餐桌（唯讀） ----
    list = requests()
    const after = stovePots(list, houseJobs())
    expect(after.left.request?.id).not.toBe(row.id)
    expect(after.right?.request?.id).not.toBe(row.id)
    const adoptedSummary = list.find((r) => r.id === row.id)!
    expect(destinationFor(adoptedSummary, potState({ request: adoptedSummary }))).toEqual({ kind: 'result', requestId: row.id })

    // ---- 客廳：今天晚餐就是這一餐；7 天長條的今天有一筆 ----
    const dinner = planForMeal(plansOn<TodayPlan>(date), 'dinner')
    expect(dinner.plan?.id).toBe(plan.id)
    const names = dishNames(dinner.plan!)
    for (const dish of table.dishes) expect(names).toContain(dish.dish_name)
    expect(names).toContain(table.staple!.label)
    expect(planRatios(dinner.plan!).energy).not.toBeNull()
    const todayBar = dayBars(trend<LivingTrendItem>(7).items, 'dinner', date).at(-1)!
    expect(todayBar).toMatchObject({ today: true, meals: 1 })
    expect(todayBar.state).not.toBe('none')

    // ---- 冰箱門：月曆有今天、當日餐單有這一餐、可以回餐桌看、趨勢算進去 ----
    const dates = planDates()
    expect(dates.find((d) => d.meal_date === date)?.count).toBe(2) // 種子的午餐＋剛採用的晚餐
    expect(latestAdoptedDate(dates, date)).toBe(date)
    const record = plansOn<AdoptedPlan>(date).find((p) => p.id === plan.id)!
    expect(isGenerationPlan(record)).toBe(true)
    expect(tablePath(record)).toBe(`/plan/${row.id}/result/1`)
    expect(planEnergy(record)).not.toBeNull()
    // 便利貼的菜名和客廳一樣要把主食算進去（主食不在菜色清單裡），而且排最前面
    expect(planDishNames(record)).toEqual([table.staple!.label, ...table.dishes.map((dish) => dish.dish_name)])
    // 一鍵購入只看「有沒有待買的東西」
    expect(canCheckout(record)).toBe(Boolean(record.prices?.items?.length || record.prices?.unquantified_items?.length))
    const normalized = normalizeTrend(trend<FridgeTrendItem>(7), 7, date)
    const points = byDay(normalized.items, normalized.since, 7)
    expect(points.at(-1)).toMatchObject({ date, meals: 2 })
    // 廚房冰箱上的便利貼用同一份趨勢
    expect(weekStats(trend<KitchenTrendItem>(7).items).meals).toBe(normalized.items.length)
  })

  it('等你決定的那一桌：先決定，才輪到能不能採用', () => {
    const row = requests().find((r) => r.status === 'awaiting_decision' && r.mode === 'questionnaire')!
    const version = results(row.id)[0].result_version
    let table = result(row.id, version)
    let flags = resultFlags(table)
    expect(statusSummary(table, flags).text).toBe('等你決定')
    const context = { cooking: false, requestLoaded: true, adoptedHere: false, canShop: false }
    expect(tableActions(table, flags, context)[0].kind).toBe('decide')
    // 熱量未知：每個座位都是「計算不完整」的空碗，不是 0
    for (const seat of buildSeats(table.nutrition)) {
      expect(seat.verdict).toBe('unknown')
      expect(seat.energy).toBeNull()
    }

    const pending = table.eligibility.pending_decisions[0]
    ok(call('POST', `/meal-requests/${row.id}/results/${version}/decisions`,
      { kind: pending.kind, request_version: table.request_version }, key()), 201)
    table = result(row.id, version)
    flags = resultFlags(table)
    expect(table.eligibility.pending_decisions).toEqual([])
    // 決定「繼續查看」不等於可以採用：缺必要營養的結果仍然不能採用，按鈕變成「看原因」
    expect(table.eligibility.adoptable).toBe(false)
    expect(tableActions(table, flags, context)[0].kind).toBe('reason')
    expect(get<MealRequest>(`/meal-requests/${row.id}`).status).toBe('result_ready')
  })

  it('失敗的那一鍋：廚房帶到 /cooking，單板第 5 步是「再煮一次」', () => {
    const summary = requests().find((r) => r.status === 'failed')!
    const job = latestGenerationJob(houseJobs(), summary.id)
    const pot = potState({ request: summary, job })
    expect(pot.kind).toBe('failed')
    expect(destinationFor(summary, pot)).toEqual({ kind: 'path', to: `/plan/${summary.id}/cooking` })
    const row = get<MealRequest>(`/meal-requests/${summary.id}`)
    expect(submitState(row, requestJob(row.id))).toMatchObject({ canSubmit: true, submitLabel: '再煮一次' })
  })
})

describe('食譜書：挑菜 → 採用 → 其他房間看得到', () => {
  beforeEach(() => { enter() })

  it('挑菜的單不上爐；採用之後客廳與冰箱門都有這一餐，而且沒有「回餐桌」這條路', () => {
    const members = get<{ members: Member[] }>('/members').members
    const staples = get<StapleOptions>('/meal-requests/staple-options')
    const date = today()
    const stoveBefore = stovePots(requests(), houseJobs())

    // 第一步：誰要吃（不選阿嬤：她的設定算不出目標）→ POST 一張挑菜的單
    const picks = initialPicks(members, [])
    for (const member of members.filter((m) => m.display_name !== '阿嬤')) picks[member.id] = { dining: true, bento: false }
    let row = ok<MealRequest>(call('POST', '/meal-requests', pickerDraft('dinner', picks, undefined, date)), 201)
    expect(row.draft.mode).toBe('picker')
    // 還在挑的單不上爐（鍋裡的料是問卷五步的進度）；單軌上點它是回食譜書
    const stoveAfter = stovePots(requests(), houseJobs())
    expect(stoveAfter.left.request?.id).toBe(stoveBefore.left.request?.id)
    expect(stoveAfter.right?.request?.id).not.toBe(row.id)
    const summary = requests().find((r) => r.id === row.id)!
    expect(destinationFor(summary, potState({ request: summary }))).toEqual({ kind: 'path', to: `/pick/${row.id}` })
    expect(requestPath(summary)).toBe(`/pick/${row.id}`)

    // 第二步：挑沒有禁忌的菜，確定（存 specified_dishes、dish_count、主食）
    const catalog = get<RecipeCatalog>(`/meal-requests/${row.id}/recipe-catalog`)
    const byName = new Map(catalog.dishes.map((d) => [d.dish_name, d]))
    const chosen = chosenNames(
      catalog.dishes.filter((d) => d.conflicts.length === 0 && d.category !== '主食' && d.meal_type !== '早餐').slice(0, 3).map((d) => d.dish_name),
      byName,
    )
    expect(chosen).toHaveLength(3)
    row = ok<MealRequest>(call('PUT', `/meal-requests/${row.id}`, {
      version: row.version,
      draft: {
        ...pickerDraft('dinner', picks, row.draft), specified_dishes: chosen, dish_count: chosen.length,
        staple: stapleFor(hasStapleDish(chosen, byName), staples),
      },
    }))
    expect(row.complete, JSON.stringify(row.issues)).toBe(true)

    // 第三步：資料庫菜單 → 營養 → 採用
    const menu = get<RecipeRetrieval>(`/meal-requests/${row.id}/recipes`)
    const recipeIds = menu.selected.map((r) => r.recipe_id)
    expect(menu.selected.map((r) => r.recipe_name).sort()).toEqual([...chosen].sort())
    const nutrition = ok<NutritionPreview>(call('POST', `/meal-requests/${row.id}/nutrition-preview`, { recipe_ids: recipeIds }))
    // 種子裡這三道菜配這三個人會通過；假後端的資料改到不通過時，這裡先壞（後面跨房間的檢查才不會悄悄被跳過）
    expect(nutrition.status).toBe('nutrition_passed')
    expect(nutrition.adoption_blocked).toBe(false)
    const adopt = call('POST', '/meal-plans', { meal_request_id: row.id, request_version: row.version, recipe_ids: recipeIds }, key())
    const plan = ok<{ meal_plan: { id: string } }>(adopt, 201).meal_plan

    // 這張單變成已採用：食譜書直接開在結果那一步、只能看
    row = get<MealRequest>(`/meal-requests/${row.id}`)
    expect(row.status).toBe('confirmed')
    expect(bookStep(row, null)).toBe(2)
    expect(get<RecipeRetrieval>(`/meal-requests/${row.id}/recipes`).adopted_plan?.id).toBe(plan.id)

    // 客廳：今天晚餐就是這一餐（資料庫菜單的數字是 number，生成結果的是字串；兩種都要讀得懂）
    const dinner = planForMeal(plansOn<TodayPlan>(date), 'dinner')
    expect(dinner.plan?.id).toBe(plan.id)
    for (const name of chosen) expect(dishNames(dinner.plan!)).toContain(name)
    expect(dayBars(trend<LivingTrendItem>(7).items, 'dinner', date).at(-1)).toMatchObject({ today: true, meals: 1 })

    // 冰箱門：當日餐單有它；它不是生成結果，所以沒有餐桌可以回
    const record = plansOn<AdoptedPlan>(date).find((p) => p.id === plan.id)!
    expect(isGenerationPlan(record)).toBe(false)
    expect(tablePath(record)).toBeNull()
    for (const name of chosen) expect(planDishNames(record)).toContain(name)
    const normalized = normalizeTrend(trend<FridgeTrendItem>(7), 7, date)
    expect(byDay(normalized.items, normalized.since, 7).at(-1)).toMatchObject({ date, meals: 2 })

    // 廚房：已採用的挑菜單一樣回食譜書（沒有結果版本，不能帶去餐桌）
    const adopted = requests().find((r) => r.id === row.id)!
    expect(destinationFor(adopted, potState({ request: adopted }))).toEqual({ kind: 'path', to: `/pick/${row.id}` })
  })
})

describe('全家福：新增、修改、刪除成員', () => {
  beforeEach(() => { enter() })

  it('表單 → 請求內容 → 後端收得下；修改要帶版本，舊版本是 409', () => {
    const options = get<MemberOptions>('/members/options')
    const schema = buildSchema(options, today)
    const values = { ...memberForm(null), display_name: '表姊', birth_date: '1995-03-02', weight_kg: '52' }
    const parsed = schema.safeParse(values)
    expect(parsed.success, JSON.stringify(parsed.error?.issues)).toBe(true)

    const created = ok<Member>(call('POST', '/members', toBody(values)), 201)
    expect(get<{ members: Member[] }>('/members').members.map((m) => m.id)).toContain(created.id)

    const edited = { ...memberForm(created), weight_kg: '54' }
    const updated = ok<Member>(call('PUT', `/members/${created.id}`, { ...toBody(edited), version: created.version }))
    expect(updated.weight_kg).toBe('54')
    expect(updated.version).toBe(created.version + 1)
    const stale = call('PUT', `/members/${created.id}`, { ...toBody({ ...edited, weight_kg: '56' }), version: created.version })
    expect(stale.status).toBe(409)
    expect(errorOf(stale).code).toBe('VERSION_CONFLICT')

    // 新成員馬上可以在問卷裡被選到
    const row = ok<MealRequest>(call('POST', '/meal-requests', { meal_date: today() }), 201)
    expect(Object.keys(toForm(row, get<{ members: Member[] }>('/members').members).participants)).toContain(created.id)

    expect(call('DELETE', `/members/${created.id}`).status).toBe(204)
    expect(get<{ members: Member[] }>('/members').members.map((m) => m.id)).not.toContain(created.id)
  })
})

describe('飲食小幫手：只轉述後端的回答與出處', () => {
  beforeEach(() => { enter() })

  type Event = { event: string; data: unknown }
  /** 和 AssistantProvider 收串流的方式一樣，把事件收成一則訊息。 */
  function answerOf(events: Event[], id: number): Message {
    const message: Message = { id, role: 'assistant', text: '', status: 'streaming' }
    for (const { event, data } of events) {
      if (event === 'meta') message.type = (data as { type: AssistantType }).type
      if (event === 'references') message.references = data as AssistantReference[]
      if (event === 'delta') message.text += (data as { text: string }).text
      if (event === 'done') {
        message.citations = (data as { citations?: number[] }).citations
        message.status = 'done'
      }
    }
    return message
  }

  it('有出處的回答列出引用；醫療問題不回答、沒有出處；前文只帶完成的訊息', () => {
    const question: Message = { id: 1, role: 'user', text: '蛋白質要怎麼吃？', status: 'done' }
    const sse = call('POST', '/assistant/chat', { message: question.text, history: buildHistory([]), engine: 'local' }).sse!
    const answer = answerOf(sse, 2)
    expect(answer.status).toBe('done')
    expect(answer.type).toBe('answer')
    const cited = citedReferences(answer)
    expect(cited.length).toBeGreaterThan(0)
    for (const reference of cited) expect(answer.text).toContain(`[${reference.n}]`)
    expect(answerBadge(answer.type)).toBeNull()

    const history = buildHistory([question, answer])
    expect(history.map((turn) => turn.role)).toEqual(['user', 'assistant'])
    const medical = answerOf(call('POST', '/assistant/chat', { message: '高血壓的藥劑量要吃多少？', history, engine: 'local' }).sse!, 4)
    expect(medical.type).toBe('declined_medical')
    expect(citedReferences(medical)).toEqual([])
    expect(answerBadge(medical.type)).not.toBeNull()
  })
})

describe('換家庭、換人、登出：不留上一個家的資料', () => {
  it('後端：換到外婆家後讀到的是外婆家的單；登出後一律 401', () => {
    enter('林家廚房')
    const lin = requests().map((r) => r.id)
    enter('外婆家')
    const grandma = requests().map((r) => r.id)
    expect(grandma.some((id) => lin.includes(id))).toBe(false)
    expect(call('POST', '/auth/logout').status).toBe(204)
    expect(call('GET', '/auth/me').status).toBe(401)
    expect(call('GET', '/members').status).toBe(401)
  })

  function filled(): QueryClient {
    const client = new QueryClient()
    client.setQueryData(['me'], { user: { id: 'u', email: 'a@example.com' }, current_household: { id: 'h1', name: '林家廚房' } })
    client.setQueryData(['households'], { households: [], current_household_id: 'h1' })
    client.setQueryData(['staple-options'], { options: [] })
    client.setQueryData(['team-status'], {})
    client.setQueryData(['team-health'], {})
    client.setQueryData(mealRequestsQuery.queryKey, [])
    client.setQueryData(['meal-request', 'r1'], {})
    client.setQueryData(['members'], [])
    client.setQueryData(generationJobsQuery.queryKey, [])
    client.setQueryData(['jobs', 'r1'], null)
    client.setQueryData(['meal-results', 'r1'], [])
    client.setQueryData(['meal-result', 'r1', 1], {})
    client.setQueryData(planDatesQuery.queryKey, [])
    client.setQueryData(plansOnDateQuery('2026-11-14').queryKey, [])
    client.setQueryData(trendQuery(7, '2026-11-14').queryKey, { since: '', until: '', items: [] })
    client.setQueryData(['recipe-catalog', 'r1'], {})
    client.setQueryData(['member-target', 'm1', 1], {})
    return client
  }
  const firstKeys = (client: QueryClient) => [...new Set(client.getQueryCache().getAll().map((q) => String(q.queryKey[0])))].sort()

  it('換家庭：只留和家庭無關的快取', async () => {
    const client = filled()
    await afterHouseholdChange(client)
    expect(firstKeys(client)).toEqual(['households', 'me', 'staple-options', 'team-health', 'team-status'])
  })

  it('換人登入（上一個人是登入逾期被請出門，沒按登出）：家庭資料與家庭清單都清掉', () => {
    const client = filled()
    const next: Me = { user: { id: 'u2', email: 'b@example.com' }, current_household: null }
    afterLogin(client, next)
    expect(client.getQueryData(['me'])).toEqual(next)
    expect(firstKeys(client)).toEqual(['me', 'staple-options', 'team-health', 'team-status'])
  })

  it('登出：全部清掉', () => {
    const client = filled()
    afterLogout(client)
    expect(client.getQueryCache().getAll()).toEqual([])
  })

  it('採用之後：客廳、廚房便利貼、冰箱門讀的那幾份都標成過期，其他的不動', async () => {
    const client = filled()
    await invalidateAdoptedPlans(client)
    const stale = client.getQueryCache().getAll().filter((q) => q.state.isInvalidated).map((q) => String(q.queryKey[0])).sort()
    expect(stale).toEqual(['meal-plan-dates', 'meal-plan-trend', 'meal-plans', 'meal-requests'])
  })
})
