import { describe, expect, it } from 'vitest'
import {
  addDays, axisLabels, barView, byDay, canCheckout, dayEntry, dayLabel, describePoint, hasPartialBars, marksMayBeIncomplete,
  monthCells, normalizeTrend, num, parseDay, planDishNames, planEnergy, requestNote, requestPath, requestTitle, scaleTop,
  shiftMonth, shownMonth, tablePath, trendStats, validDay, weekdayLabel,
  type AdoptedPlan, type TrendItem,
} from './records'

const item = (over: Partial<TrendItem>): TrendItem => ({
  meal_date: '2026-11-10', meal_type: 'dinner', energy_kcal: null, protein_g: null,
  target_energy_kcal: null, target_protein_g: null, ...over,
})

const plan = (over: Partial<AdoptedPlan>): AdoptedPlan => ({
  id: 'p1', meal_date: '2026-11-14', meal_type: 'dinner', mode: 'database_menu', status: 'adopted',
  adopted_at: '2026-11-14T10:00:00Z', meal_request_id: 'r1', dishes: [{ dish_name: '清蒸鱸魚' }], ...over,
})

describe('num', () => {
  it('字串、分數與數字都轉成數字', () => {
    expect(num('12.5')).toBe(12.5)
    expect(num('3/4')).toBe(0.75)
    expect(num(7)).toBe(7)
    expect(num('0')).toBe(0)
  })

  it('未知就是 null，不會變成 0 或 NaN', () => {
    expect(num(null)).toBeNull()
    expect(num(undefined)).toBeNull()
    expect(num('')).toBeNull()
    expect(num('abc')).toBeNull()
    expect(num(Number.NaN)).toBeNull()
  })
})

describe('日期', () => {
  it('只接受存在的日期', () => {
    expect(parseDay('2026-11-14')?.getDate()).toBe(14)
    expect(parseDay('2026-02-30')).toBeNull()
    expect(parseDay('2026-1-5')).toBeNull()
    expect(validDay('2026-11-14')).toBe('2026-11-14')
    expect(validDay('亂寫')).toBeNull()
    expect(validDay(null)).toBeNull()
  })

  it('加減天數會跨月、跨年', () => {
    expect(addDays('2026-11-30', 1)).toBe('2026-12-01')
    expect(addDays('2026-01-01', -1)).toBe('2025-12-31')
    expect(addDays('2026-11-14', -6)).toBe('2026-11-08')
  })

  it('標籤', () => {
    expect(dayLabel('2026-11-04')).toBe('11/4')
    expect(weekdayLabel('2026-11-14')).toBe('週六')
  })
})

describe('月曆', () => {
  it('週日開頭，前後補空格湊滿整週', () => {
    // 2026-11-01 是週日，11 月 30 天 → 5 週
    const november = monthCells('2026-11')
    expect(november).toHaveLength(35)
    expect(november[0]).toBe('2026-11-01')
    expect(november[29]).toBe('2026-11-30')
    expect(november.slice(30)).toEqual([null, null, null, null, null])
    // 2026-08-01 是週六，31 天 → 6 週
    const august = monthCells('2026-08')
    expect(august).toHaveLength(42)
    expect(august.slice(0, 6)).toEqual([null, null, null, null, null, null])
    expect(august[6]).toBe('2026-08-01')
  })

  it('二月只有四週時不多畫一排', () => {
    // 2026-02-01 是週日，28 天
    expect(monthCells('2026-02')).toHaveLength(28)
  })

  it('翻月份會跨年', () => {
    expect(shiftMonth('2026-12', 1)).toBe('2027-01')
    expect(shiftMonth('2026-01', -1)).toBe('2025-12')
  })

  it('翻到別的月份只在選的日期沒變時有效', () => {
    expect(shownMonth('2026-11-14', null)).toBe('2026-11')
    expect(shownMonth('2026-11-14', { forDate: '2026-11-14', month: '2026-09' })).toBe('2026-09')
    // 之後又選了別的日期：跟著日期走
    expect(shownMonth('2026-10-02', { forDate: '2026-11-14', month: '2026-09' })).toBe('2026-10')
  })

  it('日期清單滿 60 筆、而且看的月份不比最早那筆新，才提醒貼紙可能不完整', () => {
    const few = [{ meal_date: '2026-11-01', count: 1 }]
    expect(marksMayBeIncomplete(few, '2026-01')).toBe(false)
    const full = Array.from({ length: 60 }, (_, i) => ({ meal_date: addDays('2026-11-14', -i), count: 1 }))
    expect(marksMayBeIncomplete(full, '2026-11')).toBe(false)
    expect(marksMayBeIncomplete(full, '2026-09')).toBe(true)
    expect(marksMayBeIncomplete(full, '2026-08')).toBe(true)
  })
})

describe('趨勢', () => {
  it('兩種快取形狀都接得住', () => {
    const whole = normalizeTrend({ since: '2026-11-08', until: '2026-11-14', items: [item({})] }, 7, '2026-11-14')
    expect(whole).toMatchObject({ since: '2026-11-08', until: '2026-11-14' })
    expect(whole.items).toHaveLength(1)
    const bare = normalizeTrend([item({}), item({})], 7, '2026-11-14')
    expect(bare).toMatchObject({ since: '2026-11-08', until: '2026-11-14' })
    expect(bare.items).toHaveLength(2)
    expect(normalizeTrend(undefined, 30, '2026-11-14')).toEqual({ since: '2026-10-16', until: '2026-11-14', items: [] })
  })

  it('逐日加總；沒有紀錄的日子留空', () => {
    const points = byDay([
      item({ meal_date: '2026-11-09', energy_kcal: '600', target_energy_kcal: '650', cost_total: '200', budget: '300' }),
      item({ meal_date: '2026-11-09', energy_kcal: 500, target_energy_kcal: 550, cost_total: '150.5', budget: '300' }),
    ], '2026-11-08', 3)
    expect(points.map((p) => p.date)).toEqual(['2026-11-08', '2026-11-09', '2026-11-10'])
    expect(points[0]).toMatchObject({ meals: 0, energy: null, cost: null, unknownCost: false })
    expect(points[1]).toMatchObject({ meals: 2, energy: 1100, target: 1200, cost: 350.5, budget: 600, unknownEnergy: false, unknownCost: false })
  })

  it('未知不會被加成 0：缺價以已知部分計並標記，完全未知就是 null', () => {
    const points = byDay([
      item({ meal_date: '2026-11-08', energy_kcal: '700', cost_total: null, known_cost_total: '120' }),
      item({ meal_date: '2026-11-08', energy_kcal: null, cost_total: '80' }),
      // 資料庫菜單的紀錄：沒有成本欄位
      item({ meal_date: '2026-11-09', energy_kcal: 640 }),
      // 只有菜名的最小紀錄
      item({ meal_date: '2026-11-10' }),
    ], '2026-11-08', 3)
    expect(points[0]).toMatchObject({ energy: 700, unknownEnergy: true, cost: 200, unknownCost: true })
    expect(points[1]).toMatchObject({ energy: 640, unknownEnergy: false, cost: null, unknownCost: true })
    expect(points[2]).toMatchObject({ meals: 1, energy: null, unknownEnergy: true, cost: null, unknownCost: true })
  })

  it('範圍外的紀錄不算', () => {
    expect(byDay([item({ meal_date: '2026-12-01', energy_kcal: 1 })], '2026-11-08', 3).every((p) => p.meals === 0)).toBe(true)
  })

  it('統計：達成率只算有實際值與目標的餐，成本只算價格完整的餐', () => {
    const stats = trendStats([
      item({ energy_kcal: '660', target_energy_kcal: '600', protein_g: '27', target_protein_g: '30', cost_total: '200', budget: '250' }),
      item({ energy_kcal: '540', target_energy_kcal: '600', protein_g: null, target_protein_g: '30', cost_total: '300', budget: '250' }),
      item({ energy_kcal: null, target_energy_kcal: '600', cost_total: null, known_cost_total: '90', budget: '250' }),
      item({ energy_kcal: 500 }),
    ])
    expect(stats.meals).toBe(4)
    expect(stats.energyReach).toBeCloseTo(1.0)
    expect(stats.proteinReach).toBeCloseTo(0.9)
    expect(stats.pricedMeals).toBe(2)
    expect(stats.averageCost).toBe(250)
    expect(stats.underBudget).toBe(1)
    expect(stats.unknownCostMeals).toBe(2)
    expect(stats.partialCostMeals).toBe(1)
  })

  it('沒有可比較的紀錄時是 null，不是 0', () => {
    const stats = trendStats([item({}), item({ energy_kcal: '500', target_energy_kcal: '0' })])
    expect(stats.energyReach).toBeNull()
    expect(stats.proteinReach).toBeNull()
    expect(stats.averageCost).toBeNull()
    expect(trendStats([])).toMatchObject({ meals: 0, energyReach: null, averageCost: null, underBudget: 0 })
  })

  it('長條：未知不畫高度；只知道一部分要標記；刻度含目標', () => {
    const points = byDay([
      item({ meal_date: '2026-11-08', cost_total: '200', budget: '400' }),
      item({ meal_date: '2026-11-09', cost_total: null, known_cost_total: '100', budget: '400' }),
      item({ meal_date: '2026-11-10', cost_total: null }),
    ], '2026-11-08', 3)
    const top = scaleTop(points, 'cost')
    expect(top).toBe(460)
    expect(barView(points[0], 'cost', top)).toEqual({ height: (200 / 460) * 100, tick: (400 / 460) * 100, partial: false })
    expect(barView(points[1], 'cost', top)).toMatchObject({ partial: true })
    expect(barView(points[2], 'cost', top)).toEqual({ height: null, tick: null, partial: false })
    // 熱量全未知：刻度給 10，避免除以 0
    expect(scaleTop(points, 'energy')).toBe(10)
    expect(barView(points[0], 'energy', 10).height).toBeNull()
  })

  it('圖上有沒有「只計已知部分」的長條：成本和熱量各看各的，整天未知的不算', () => {
    // 同一天一餐有完整成本、另一餐是資料庫菜單（沒有成本欄位）：沒有任何一餐是「缺一部分價格」，長條照樣只算到一餐
    const mixedCost = byDay([
      item({ meal_date: '2026-11-08', energy_kcal: '700', cost_total: '200' }),
      item({ meal_date: '2026-11-08', energy_kcal: '650' }),
    ], '2026-11-08', 2)
    expect(trendStats([item({ cost_total: '200' }), item({})]).partialCostMeals).toBe(0)
    expect(hasPartialBars(mixedCost, 'cost')).toBe(true)
    expect(hasPartialBars(mixedCost, 'energy')).toBe(false)

    // 同一天一餐熱量已知、一餐未知
    const mixedEnergy = byDay([
      item({ meal_date: '2026-11-08', energy_kcal: '700', cost_total: '200' }),
      item({ meal_date: '2026-11-08', energy_kcal: null, cost_total: '150' }),
    ], '2026-11-08', 2)
    expect(hasPartialBars(mixedEnergy, 'energy')).toBe(true)
    expect(hasPartialBars(mixedEnergy, 'cost')).toBe(false)

    // 整天都未知：不畫長條，也就沒有「部分」
    expect(hasPartialBars(byDay([item({ meal_date: '2026-11-08' })], '2026-11-08', 2), 'energy')).toBe(false)
    expect(hasPartialBars(byDay([], '2026-11-08', 2), 'cost')).toBe(false)
  })

  it('每天的文字說明把未知寫成未知', () => {
    const [known, partial, unknown] = byDay([
      item({ meal_date: '2026-11-08', energy_kcal: '1820', target_energy_kcal: '1900', cost_total: '412', budget: '500' }),
      item({ meal_date: '2026-11-09', cost_total: null, known_cost_total: '100' }),
      item({ meal_date: '2026-11-10' }),
    ], '2026-11-08', 3)
    expect(describePoint(known, 'energy')).toBe('11/8・1 餐，熱量 1,820 kcal，目標 1,900 kcal')
    expect(describePoint(known, 'cost')).toBe('11/8・1 餐，按比例成本 412 元，預算 500 元')
    expect(describePoint(partial, 'cost')).toBe('11/9・1 餐，按比例成本 100 元（僅已知部分）')
    expect(describePoint(unknown, 'cost')).toBe('11/10・1 餐，按比例成本 未知')
    expect(describePoint(unknown, 'energy')).toBe('11/10・1 餐，熱量 未知')
  })

  it('時間軸標籤：7 天全標，更長只標頭中尾', () => {
    expect(axisLabels(byDay([], '2026-11-08', 7))).toHaveLength(7)
    expect(axisLabels(byDay([], '2026-10-16', 30))).toEqual(['10/16', '10/30', '11/14'])
  })
})

describe('已採用的餐單', () => {
  type Prices = NonNullable<AdoptedPlan['prices']>
  const prices = (over: Partial<Prices>): Prices => ({
    status: 'computed', budget: '500', budget_status: 'OK', price_complete: true, purchase_total: '600', cost_total: '300',
    known_purchase_total: '600', known_cost_total: '300', items: [], ...over,
  })
  const priceItem = { food_id: 'f1', name: '鱸魚', price_status: 'known' } as Prices['items'][number]

  it('一鍵購入：資料庫菜單看後端標示的缺料快照', () => {
    expect(canCheckout(plan({ market_shortages_available: true }))).toBe(true)
    expect(canCheckout(plan({ market_shortages_available: false }))).toBe(false)
    expect(canCheckout(plan({}))).toBe(false)
  })

  it('一鍵購入：生成結果要有待買或無法換算的食材', () => {
    expect(canCheckout(plan({ result_version: 2, prices: prices({ items: [priceItem] }) }))).toBe(true)
    expect(canCheckout(plan({ result_version: 2, prices: prices({ unquantified_items: [{ food_id: 'f2', name: '鹽' }] }) }))).toBe(true)
    expect(canCheckout(plan({ result_version: 2, prices: prices({}) }))).toBe(false)
    expect(canCheckout(plan({ result_version: 2 }))).toBe(false)
    // 生成結果的紀錄不看資料庫菜單的旗標
    expect(canCheckout(plan({ result_version: 2, market_shortages_available: true }))).toBe(false)
  })

  it('一鍵購入：不再是採用中的餐單不提供', () => {
    expect(canCheckout(plan({ status: 'superseded', market_shortages_available: true }))).toBe(false)
  })

  it('只有生成結果的紀錄可以回餐桌看那一版', () => {
    expect(tablePath(plan({ result_version: 3 }))).toBe('/plan/r1/result/3')
    expect(tablePath(plan({ result_version: null }))).toBeNull()
    expect(tablePath(plan({ result_version: 3, meal_request_id: null }))).toBeNull()
  })

  it('整餐熱量：未知是 null', () => {
    expect(planEnergy(plan({ snapshot: { nutrition: { totals: { energy_kcal: '1234/2' } } } }))).toBe(617)
    expect(planEnergy(plan({ snapshot: {}, energy_kcal: null }))).toBeNull()
    expect(planEnergy(plan({ result_version: 1 }))).toBeNull()
    expect(planEnergy(plan({ result_version: 1, nutrition: { energy_kcal: '2340' } as AdoptedPlan['nutrition'] }))).toBe(2340)
  })

  it('菜名：資料庫菜單把主食排最前面', () => {
    expect(planDishNames(plan({ snapshot: { staple_choice: '五穀飯' } }))).toEqual(['五穀飯', '清蒸鱸魚'])
    expect(planDishNames(plan({}))).toEqual(['清蒸鱸魚'])
  })

  it('菜名：生成結果的主食在 staple.label，一樣要列出來', () => {
    const staple = { label: '白飯' } as AdoptedPlan['staple']
    expect(planDishNames(plan({ result_version: 2, staple }))).toEqual(['白飯', '清蒸鱸魚'])
    // 選「不需要主食」：後端回 null
    expect(planDishNames(plan({ result_version: 2, staple: null }))).toEqual(['清蒸鱸魚'])
    expect(planDishNames(plan({ result_version: 2 }))).toEqual(['清蒸鱸魚'])
    // 生成結果不看快照裡的欄位；菜色清單已經有同名的就不重複
    expect(planDishNames(plan({ result_version: 2, snapshot: { staple_choice: '五穀飯' } }))).toEqual(['清蒸鱸魚'])
    expect(planDishNames(plan({ result_version: 2, staple, dishes: [{ dish_name: '白飯' }] }))).toEqual(['白飯'])
  })

  it('物件列的「當日餐單」：一餐直接開明細，好幾餐開清單，沒有就回便利貼', () => {
    expect(dayEntry([])).toEqual({ kind: 'note' })
    expect(dayEntry([{ id: 'p1' }])).toEqual({ kind: 'plan', id: 'p1' })
    expect(dayEntry([{ id: 'p1' }, { id: 'p2' }])).toEqual({ kind: 'day' })
  })
})

describe('生成紀錄', () => {
  it('食譜庫選菜回食譜書，其餘回出菜口', () => {
    expect(requestPath({ id: 'a', mode: 'picker' })).toBe('/pick/a')
    expect(requestPath({ id: 'a', mode: 'questionnaire' })).toBe('/plan/a')
  })

  it('標題與說明', () => {
    expect(requestTitle({ meal_date: '2026-11-15', meal_type: 'lunch' })).toBe('11/15（週日）午餐')
    expect(requestTitle({ meal_date: null, meal_type: null })).toBe('未選日期未選餐別')
    expect(requestNote({ mode: 'picker', version: 4 })).toBe('食譜庫選菜')
    expect(requestNote({ mode: 'questionnaire', version: 2 })).toBe('草稿第 2 版')
  })
})
