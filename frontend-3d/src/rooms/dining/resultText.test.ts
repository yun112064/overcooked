import { describe, expect, it } from 'vitest'
import type { NutritionSuggestion, ResultDecision, RetryState } from '../../api/types'
import {
  adoptBody, barTone, budgetStamp, dailyPercent, dailyRef, fmt, grams, groupIngredients, highSodium, ingredientAmount, macroRows,
  missingReason, monthDay, parseVersion, previousDayText, producedVersion, retryActive, retryCommandName, stapleAmount, stapleProblem,
  stepLines, suggestionText, volume,
} from './resultText'
import { mealResult } from './testData'

const retry = (status: RetryState['status'], produced: number | null = null): RetryState => ({
  job_id: 'j', status, stage: null, attempts: 1, error: null, produced_result_version: produced, created_at: '',
})

describe('parseVersion', () => {
  it('只接受正整數', () => {
    expect(parseVersion('3')).toBe(3)
    expect(parseVersion('12')).toBe(12)
    for (const bad of [undefined, '', '0', '-1', '1.5', 'latest', '03', '1e3', '99999999999999999999']) {
      expect(parseVersion(bad)).toBeNull()
    }
  })
})

describe('用量', () => {
  it('克數取整；未知寫無法換算，不寫 0', () => {
    expect(grams('299.6')).toBe('300 g')
    expect(grams('1/3')).toBe('0 g')
    expect(grams(null)).toBe('無法換算')
    expect(grams(undefined)).toBe('無法換算')
    expect(volume('250')).toBe('250 mL')
    expect(volume('abc')).toBe('無法換算')
  })

  it('量匙取到 ¼，公升與其他單位原樣，沒有數量用原文', () => {
    const base = { name: 'x', group: null, display_amount: null, display_unit: null, display_text: null, display_grams: null }
    expect(ingredientAmount({ ...base, display_amount: '1.5', display_unit: '大匙' })).toBe('1½ 大匙')
    expect(ingredientAmount({ ...base, display_amount: '0.25', display_unit: '小匙' })).toBe('¼ 小匙')
    expect(ingredientAmount({ ...base, display_amount: '2', display_unit: '顆' })).toBe('2 顆')
    expect(ingredientAmount({ ...base, display_amount: '1.20', display_unit: 'L' })).toBe('1.2 L')
    expect(ingredientAmount({ ...base, display_amount: '300', display_unit: 'g' })).toBe('300 g')
    expect(ingredientAmount({ ...base, display_text: '少許' })).toBe('少許')
    expect(ingredientAmount({ ...base, display_grams: '12.4' })).toBe('12 g')
    expect(ingredientAmount(base)).toBe('無法換算')
  })

  it('食材分組：沒有分組的排最前面，其餘照出現順序', () => {
    const groups = groupIngredients([
      { name: '鹽', group: '調味' }, { name: '魚', group: null }, { name: '薑', group: '醃料' }, { name: '蔥' }, { name: '醬油', group: '調味' },
    ])
    expect(groups.map((g) => g.group)).toEqual(['', '調味', '醃料'])
    expect(groups[0].items.map((i) => i.name)).toEqual(['魚', '蔥'])
    expect(groups[1].items.map((i) => i.name)).toEqual(['鹽', '醬油'])
    expect(groupIngredients([])).toEqual([])
  })

  it('步驟：有展開過的文字而且句數相同才用它', () => {
    expect(stepLines(['加[調味]'], ['加鹽'])).toEqual(['加鹽'])
    expect(stepLines(['a', 'b'], ['只有一句'])).toEqual(['a', 'b'])
    expect(stepLines(['a'])).toEqual(['a'])
  })
})

describe('高鈉與主食', () => {
  it('高鈉提醒寫出毫克與占每日上限的比例；沒有數值就不提醒', () => {
    expect(highSodium(true, '960')).toEqual({ tag: '高鈉・960 mg', detail: '每人份約 960 mg 鈉，約占每日上限 2400 mg 的 40%' })
    expect(highSodium(true, null)).toBeNull()
    expect(highSodium(false, '960')).toBeNull()
    expect(highSodium(undefined, undefined)).toBeNull()
  })

  it('主食算不出來時依原因說明', () => {
    expect(stapleProblem('藜麥飯', 'staple_not_supported')).toContain('不在可選清單中')
    expect(stapleProblem('藜麥飯', 'no_tfda_cooked_sample')).toContain('目前沒有營養資料')
    expect(stapleProblem('藜麥飯', 'staple_without_tfda_sample')).toContain('目前沒有營養資料')
    expect(stapleProblem('藜麥飯', null)).toBe('主食「藜麥飯」無法換算克數，整餐營養無法計算')
  })

  it('主食份量；沒有份量回 null', () => {
    const staple = mealResult().staple!
    expect(stapleAmount(staple)).toBe('糙米飯 640 g')
    expect(stapleAmount({ ...staple, tfda_name: null, label: '白飯', display_unit: null })).toBe('白飯 640')
    expect(stapleAmount({ ...staple, display_amount: null })).toBeNull()
  })
})

describe('價格', () => {
  it('查價失敗原因有對照表，沒對到用通用說法', () => {
    expect(missingReason('PRICE_EXPIRED')).toBe('價格已過期')
    expect(missingReason('SOMETHING_NEW')).toBe('賣場找不到相符的商品')
    expect(missingReason(null)).toBe('賣場找不到相符的商品')
  })

  it('印章：預算內、超出預算才蓋，而且寫明比的是這餐吃掉的那一個金額；價格不完整、還沒算都不蓋', () => {
    const prices = mealResult().prices
    expect(budgetStamp({ ...prices, budget_status: 'OK' })).toEqual({ tone: 'ok', text: '預算內', full: '吃掉的在預算內' })
    expect(budgetStamp({ ...prices, budget_status: 'BUDGET_EXCEEDED' })).toEqual({ tone: 'over', text: '超出預算', full: '吃掉的超出預算' })
    expect(budgetStamp({ ...prices, budget_status: 'PRICE_INCOMPLETE' })).toBeNull()
    expect(budgetStamp({ ...prices, budget_status: null })).toBeNull()
    expect(budgetStamp({ ...prices, status: 'unavailable', budget_status: 'OK' })).toBeNull()
  })

  it('印章：沒有預算就不蓋——後端在沒有預算、價格完整時回的狀態也是 OK', () => {
    const prices = mealResult().prices
    expect(budgetStamp({ ...prices, budget: null, budget_status: 'OK' })).toBeNull()
    expect(budgetStamp({ ...prices, budget: null, budget_status: 'PRICE_INCOMPLETE' })).toBeNull()
  })
})

describe('previousDayText', () => {
  it('沒有紀錄、有紀錄沒排除、有排除（寫出是哪一餐）', () => {
    const base = { previous_date: '2026-11-13', has_records: false, excluded_dishes: [], specified_repeats: [] }
    expect(previousDayText(base)).toBe('昨天（2026-11-13）與今天其他餐都沒有已採用紀錄，未排除任何菜。')
    expect(previousDayText({ ...base, has_records: true })).toBe('已比對昨天（2026-11-13）與今天其他餐的已採用紀錄；沒有需要排除的菜。')
    expect(previousDayText({
      ...base, has_records: true,
      excluded_dishes: [
        { standard_dish_id: 'a', dish_name: '滷肉', aliases: [] },
        { standard_dish_id: 'b', dish_name: '蛋餅', aliases: [], from: 'breakfast' },
      ],
    })).toBe('已比對昨天（2026-11-13）與今天其他餐的已採用紀錄；已排除：滷肉（昨天）、蛋餅（今天早餐）。')
  })

  it('舊結果沒有昨天的日期時不把 null 印出來', () => {
    expect(previousDayText({ previous_date: null, has_records: false, excluded_dishes: [], specified_repeats: [] }))
      .toBe('昨天與今天其他餐都沒有已採用紀錄，未排除任何菜。')
  })
})

describe('suggestionText', () => {
  it('營養超出範圍：差額與可調範圍由每份換算成整餐', () => {
    const suggestion: NutritionSuggestion = {
      code: 'NUTRITION_OUT_OF_RANGE', nutrient: 'protein_g', direction: 'increase', needed_change: '2.5', within_adjustable_ranges: false,
      ingredients: [
        { food_id: 'a', name: '豆腐', direction: 'increase', adjustment_approved: true, adjustable_min: '50', adjustable_max: '80', max_change: '1.2' },
        { food_id: 'b', name: '雞蛋', direction: 'increase', adjustment_approved: false },
      ],
    }
    expect(suggestionText(suggestion, 4)).toBe(
      '蛋白質需增加約 10.0 g；豆腐增加（可調 200 g–320 g，最多4.8 g）、雞蛋增加（未審核可調範圍）。已審核範圍內補不足。',
    )
  })

  it('沒有可調的食材、範圍內補得足但互相牴觸', () => {
    expect(suggestionText({
      code: 'NUTRITION_OUT_OF_RANGE', nutrient: 'energy_kcal', direction: 'decrease', needed_change: '30', within_adjustable_ranges: true, ingredients: [],
    }, 2)).toBe('熱量需減少約 60.0 kcal；沒有可調整的食材。已審核範圍內補得足，但與另一項營養無法同時達成。')
  })

  it('可調範圍未知時寫無法換算，不寫 0', () => {
    expect(suggestionText({
      code: 'NUTRITION_OUT_OF_RANGE', nutrient: 'energy_kcal', direction: 'decrease', needed_change: '30',
      ingredients: [{ food_id: 'a', name: '油', direction: 'decrease', adjustment_approved: true, adjustable_min: null, adjustable_max: '10', max_change: null }],
    }, 1)).toContain('油減少（可調 無法換算–10 g，最多未知）')
  })

  it('無法換算的食材：主食另外說明，其餘列在動作後面', () => {
    expect(suggestionText({
      code: 'INGREDIENT_UNRESOLVED', action: '請補齊克數',
      ingredients: [
        { food_id: 'a', name: '藜麥飯', reason: 'staple_not_supported' },
        { food_id: 'b', name: '香菜' }, { food_id: 'c', name: '九層塔', reason: 'no_unit' },
      ],
    }, 3)).toBe('主食「藜麥飯」不在可選清單中，整餐營養無法計算；請改選清單中的主食後重新生成；請補齊克數：香菜、九層塔')
  })
})

describe('每人營養的換算', () => {
  it('取整：kcal、mg 到整數，其餘到 0.1', () => {
    expect(fmt(759.6, 'kcal')).toBe('760')
    expect(fmt(26.04, 'g')).toBe('26.0')
    expect(fmt(699.5, 'mg')).toBe('700')
  })

  it('每日建議：建議值、範圍、上限', () => {
    expect(dailyRef({ kind: 'target', value: '2000', label: '' }, 'kcal')).toEqual({ text: '2000 kcal', base: 2000, low: 2000, high: 2000 })
    expect(dailyRef({ kind: 'range', min: '250', max: '300', label: '' }, 'g')).toEqual({ text: '250–300 g', base: 300, low: 250, high: 300 })
    expect(dailyRef({ kind: 'max', max: '2400', label: '' }, 'mg')).toEqual({ text: '< 2400 mg', base: 2400, low: null, high: 2400 })
    expect(dailyRef(undefined, 'g')).toBeNull()
    expect(dailyRef({ kind: 'range', label: '' }, 'g')).toBeNull()
  })

  it('占每日的百分比；範圍寫成兩個數字', () => {
    expect(dailyPercent(800, dailyRef({ kind: 'target', value: '2000', label: '' }, 'kcal')!)).toBe('40%')
    expect(dailyPercent(150, dailyRef({ kind: 'range', min: '250', max: '300', label: '' }, 'g')!)).toBe('50–60%')
    expect(dailyPercent(1200, dailyRef({ kind: 'max', max: '2400', label: '' }, 'mg')!)).toBe('50%')
  })

  it('上限類超過這一餐的份額、超過每日上限時換顏色並附文字', () => {
    expect(barTone('max', 0.3, 0.4)).toEqual({ tone: 'normal', note: null })
    expect(barTone('max', 0.5, 0.4)).toEqual({ tone: 'high', note: '超過這一餐的份額' })
    expect(barTone('max', 1.2, 0.4)).toEqual({ tone: 'over', note: '超過每日上限' })
    expect(barTone('target', 1.5, 0.4)).toEqual({ tone: 'normal', note: null })
    expect(barTone('range', 0.9, 0.4)).toEqual({ tone: 'normal', note: null })
  })
})

describe('macroRows', () => {
  it('三大營養素的熱量比例與偏高偏低', () => {
    const rows = macroRows({ ...mealResult().nutrition, protein_g: '50', other_nutrients: { carbohydrate_g: '300', fat_g: '50' } })!
    expect(rows.map((r) => r.label)).toEqual(['碳水', '蛋白質', '脂肪'])
    // 1200 + 200 + 450 = 1850 kcal
    expect(rows.map((r) => Math.round(r.percent))).toEqual([65, 11, 24])
    expect(rows.map((r) => r.off)).toEqual(['偏高', null, null])
    expect(rows.reduce((sum, r) => sum + r.percent, 0)).toBeCloseTo(100, 6)
  })

  it('任何一項未知就不算，不拿 0 代替', () => {
    const nutrition = mealResult().nutrition
    expect(macroRows({ ...nutrition, protein_g: null, other_nutrients: { carbohydrate_g: '300', fat_g: '50' } })).toBeNull()
    expect(macroRows({ ...nutrition, protein_g: '50', other_nutrients: { carbohydrate_g: '300', fat_g: null } })).toBeNull()
    expect(macroRows({ ...nutrition, protein_g: '50', other_nutrients: { carbohydrate_g: '300' } })).toBeNull()
    expect(macroRows({ ...nutrition, protein_g: '0', other_nutrients: { carbohydrate_g: '0', fat_g: '0' } })).toBeNull()
  })
})

describe('重試', () => {
  it('排隊與執行中算進行中', () => {
    expect(retryActive(retry('queued'))).toBe(true)
    expect(retryActive(retry('running'))).toBe(true)
    expect(retryActive(retry('succeeded', 3))).toBe(false)
    expect(retryActive(retry('failed'))).toBe(false)
    expect(retryActive(null)).toBe(false)
    expect(retryActive(undefined)).toBe(false)
  })

  it('成功後產生的新版本；和目前看的相同、還沒成功都不算', () => {
    expect(producedVersion({ price: retry('succeeded', 3), knowledge: null }, 2)).toBe(3)
    expect(producedVersion({ price: retry('succeeded', 3), knowledge: retry('succeeded', 4) }, 2)).toBe(4)
    expect(producedVersion({ price: retry('succeeded', 2), knowledge: null }, 2)).toBeNull()
    expect(producedVersion({ price: retry('running'), knowledge: retry('failed') }, 2)).toBeNull()
    expect(producedVersion({ price: retry('succeeded', null), knowledge: null }, 2)).toBeNull()
    expect(producedVersion(undefined, 2)).toBeNull()
  })
})

describe('adoptBody：採用時送給後端的內容', () => {
  const decision = (kind: ResultDecision['kind'], id: string): ResultDecision => ({
    id, kind, result_version: 2, request_version: 3, missing_food_ids: null, produced_result_version: null, created_at: '',
  })
  const withEligibility = (accepted: boolean, decisions: ResultDecision[]) => {
    const r = mealResult()
    return { ...r, eligibility: { ...r.eligibility, price_exception_accepted: accepted, decisions } }
  }

  it('只指定問卷、結果版本與問卷版本', () => {
    expect(adoptBody('r1', withEligibility(false, []))).toEqual({ meal_request_id: 'r1', result_version: 2, request_version: 3 })
  })

  it('缺價例外已接受而且有那一筆決定：附上它的 id', () => {
    const r = withEligibility(true, [decision('continue_missing_nutrition', 'd1'), decision('accept_missing_price', 'd2')])
    expect(adoptBody('r1', r)).toEqual({ meal_request_id: 'r1', result_version: 2, request_version: 3, price_exception_decision_id: 'd2' })
  })

  it('已接受但沒有那一筆決定（缺價不必先決定）：不附；有決定但後端沒說已接受：也不附', () => {
    expect(adoptBody('r1', withEligibility(true, []))).not.toHaveProperty('price_exception_decision_id')
    expect(adoptBody('r1', withEligibility(false, [decision('accept_missing_price', 'd2')]))).not.toHaveProperty('price_exception_decision_id')
  })
})

describe('retryCommandName：重試的冪等鍵要不要換', () => {
  const state = (status: RetryState['status'], jobId = 'j1'): RetryState => ({
    job_id: jobId, status, stage: null, attempts: 1, error: null, produced_result_version: null, created_at: '',
  })

  it('同一次嘗試名字不變：還沒重試過、排隊中、執行中、已成功', () => {
    expect(retryCommandName(2, 'retry', null)).toBe('2:retry')
    expect(retryCommandName(2, 'retry', undefined)).toBe('2:retry')
    expect(retryCommandName(2, 'retry', state('queued'))).toBe('2:retry')
    expect(retryCommandName(2, 'retry', state('running'))).toBe('2:retry')
    expect(retryCommandName(2, 'knowledge-retry', state('succeeded'))).toBe('2:knowledge-retry')
  })

  it('上一次失敗了就換名字（才會真的再試一次）；每次失敗各換一次', () => {
    expect(retryCommandName(2, 'retry', state('failed', 'j1'))).toBe('2:retry:j1')
    expect(retryCommandName(2, 'retry', state('failed', 'j2'))).toBe('2:retry:j2')
    expect(retryCommandName(2, 'retry', state('failed', 'j1'))).not.toBe(retryCommandName(2, 'retry', null))
  })

  it('不同版本、不同命令各自一個名字', () => {
    expect(retryCommandName(3, 'retry', null)).not.toBe(retryCommandName(2, 'retry', null))
    expect(retryCommandName(2, 'retry', null)).not.toBe(retryCommandName(2, 'knowledge-retry', null))
  })
})

describe('monthDay', () => {
  it('YYYY-MM-DD 寫成 月/日', () => {
    expect(monthDay('2026-11-04')).toBe('11/4')
    expect(monthDay('2026-01-31')).toBe('1/31')
    expect(monthDay('明天')).toBe('明天')
  })
})
