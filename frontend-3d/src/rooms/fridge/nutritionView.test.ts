import { describe, expect, it } from 'vitest'
import type { MealPlanDetail, MealResult } from '../../api/types'
import {
  barTone, energyShare, groupIngredients, macroSplit, peopleFromResult, peopleFromSnapshot, percentText, reference,
  resultAmount, safeHref, sodiumNote, stapleAmount, stapleProblem, stepLines,
} from './nutritionView'

describe('每日建議', () => {
  it('建議值、範圍、上限各有自己的文字與基準', () => {
    expect(reference({ kind: 'target', value: '2000', label: '' }, 'kcal')).toEqual({ text: '2000 kcal', base: 2000, low: 2000, high: 2000 })
    expect(reference({ kind: 'range', min: '250', max: '300', label: '' }, 'g')).toEqual({ text: '250–300 g', base: 300, low: 250, high: 300 })
    expect(reference({ kind: 'max', max: '2400', label: '' }, 'mg')).toEqual({ text: '< 2400 mg', base: 2400, low: null, high: 2400 })
    expect(reference(undefined, 'g')).toBeNull()
    expect(reference({ kind: 'max', label: '' }, 'mg')).toBeNull()
  })

  it('百分比：範圍顯示兩端；基準不是正數就不算', () => {
    expect(percentText(100, { text: '', base: 300, low: 250, high: 300 })).toBe('33–40%')
    expect(percentText(600, { text: '', base: 2400, low: null, high: 2400 })).toBe('25%')
    expect(percentText(600, { text: '', base: 0, low: 0, high: 0 })).toBeNull()
  })

  it('上限類超過這一餐的份額、超過整天，各有不同提醒', () => {
    expect(barTone(0.3, 0.4, 'max')).toBe('normal')
    expect(barTone(0.5, 0.4, 'max')).toBe('overMeal')
    expect(barTone(1.2, 0.4, 'max')).toBe('overDay')
    expect(barTone(1.2, 0.4, 'target')).toBe('normal')
  })
})

describe('每人營養', () => {
  const daily = { energy_kcal: { kind: 'target' as const, value: '2000', label: '' } }

  it('生成結果：舊結果沒有 dining 欄就當成在家吃；成員被刪掉也照樣列出', () => {
    const nutrition = {
      per_person: [
        { member_id: 'a', name: null, kinds: ['dining'], n: '1', share: '0.5', meal_ratio: '0.4', intake: { energy_kcal: '760' }, known_partial: {}, daily },
        {
          member_id: 'b', name: '小安', kinds: ['bento'], dining: false, n: '1', share: '0.5', meal_ratio: '0.4', intake: {}, known_partial: {}, daily,
          bento: { n: '0.5', share: '0.2', meal_ratio: '0.15', intake: { energy_kcal: null }, known_partial: { energy_kcal: '120' }, meal: 'lunch' },
        },
      ],
    } as unknown as MealResult['nutrition']
    const [a, b] = peopleFromResult(nutrition)
    expect(a).toMatchObject({ name: '已刪除成員', dining: { n: 1, mealRatio: 0.4 }, bento: null })
    expect(b.dining).toBeNull()
    expect(b.bento).toMatchObject({ n: 0.5, mealRatio: 0.15, meal: 'lunch' })
    expect(energyShare(a.dining!, a.daily)).toBeCloseTo(0.38)
    // 便當的熱量未知：占比也是未知
    expect(energyShare(b.bento!, b.daily)).toBeNull()
    expect(peopleFromResult({} as MealResult['nutrition'])).toEqual([])
  })

  it('資料庫菜單：附上每人分到的菜與主食', () => {
    const portion = {
      participation_id: 'x', n: '1', share: '0.5', meal: 'dinner', meal_ratio: '2/5', dish_share: '1/3',
      dish_ids: ['r1', 'r9'], staple_g: '240', intake: { energy_kcal: '700' }, known_partial: {},
    }
    const snapshot = {
      selected: [{ recipe_id: 'r1', recipe_name: '番茄炒蛋' }],
      staple_choice: '白飯',
      people: [{ member_id: 'a', name: '爸爸', kinds: ['dining'], dining: portion, bento: null, daily }],
    } as unknown as MealPlanDetail['snapshot']
    const [person] = peopleFromSnapshot(snapshot)
    expect(person.dining?.mealRatio).toBeCloseTo(0.4)
    expect(person.dining?.portionText).toBe('番茄炒蛋 0.33 份、r9 0.33 份、白飯 240 g（約 1.5 碗）')
    expect(peopleFromSnapshot({})).toEqual([])
  })
})

describe('三大營養素比例', () => {
  it('三項都已知才算', () => {
    const rows = macroSplit({ protein_g: '50', other_nutrients: { carbohydrate_g: '250', fat_g: '50' } })
    expect(rows?.map((r) => [r.label, Math.round(r.pct), r.verdict])).toEqual([['碳水', 61, '偏高'], ['蛋白質', 12, ''], ['脂肪', 27, '']])
  })

  it('有任何一項未知就不算（不畫缺一塊的長條）', () => {
    expect(macroSplit({ protein_g: '50', other_nutrients: { carbohydrate_g: null, fat_g: '50' } })).toBeNull()
    expect(macroSplit({ protein_g: null, other_nutrients: { carbohydrate_g: '1', fat_g: '1' } })).toBeNull()
    expect(macroSplit({ protein_g: '0', other_nutrients: { carbohydrate_g: '0', fat_g: '0' } })).toBeNull()
  })
})

describe('食材與步驟', () => {
  const base = { name: '鹽', group: null, display_amount: null, display_unit: null, display_text: null, display_grams: null }

  it('用量照後端取整的顯示欄位；都沒有就說無法換算，不寫 0', () => {
    expect(resultAmount({ ...base, display_amount: '1.5', display_unit: '小匙' })).toBe('1½ 小匙')
    expect(resultAmount({ ...base, display_amount: '0.25', display_unit: '大匙' })).toBe('¼ 大匙')
    expect(resultAmount({ ...base, display_amount: '1.20', display_unit: 'L' })).toBe('1.2 L')
    expect(resultAmount({ ...base, display_text: '少許' })).toBe('少許')
    expect(resultAmount({ ...base, display_grams: '152.4' })).toBe('152 g')
    expect(resultAmount(base)).toBe('無法換算')
  })

  it('沒有分組的先列，其餘照原本的順序', () => {
    const grouped = groupIngredients<{ group?: string | null; name: string }>([
      { group: '調味', name: 'a' }, { group: null, name: 'b' }, { group: '醃料', name: 'c' }, { name: 'd' },
    ])
    expect(grouped.map(([group, items]) => [group, items.map((i) => i.name)])).toEqual([['', ['b', 'd']], ['調味', ['a']], ['醃料', ['c']]])
  })

  it('步驟：筆數對得上才用展開過的文字', () => {
    expect(stepLines(['a', 'b'], ['A', 'B'])).toEqual(['A', 'B'])
    expect(stepLines(['a', 'b'], ['A'])).toEqual(['a', 'b'])
    expect(stepLines(undefined, undefined)).toEqual([])
  })

  it('高鈉提醒：鈉未知或不是高鈉都不提醒', () => {
    expect(sodiumNote(true, '960')).toEqual({ label: '高鈉・960 mg', detail: '每人份約 960 mg 鈉，約占每日上限 2400 mg 的 40%' })
    expect(sodiumNote(true, null)).toBeNull()
    expect(sodiumNote(false, '960')).toBeNull()
    expect(sodiumNote(undefined, undefined)).toBeNull()
  })

  it('主食的份量：用 TFDA 食材名＋數量＋單位；算不出來是 null，不是 0', () => {
    const staple = { label: '白飯', tfda_name: '白飯（蓬萊米）', display_amount: '600', display_unit: 'g' }
    expect(stapleAmount(staple)).toBe('白飯（蓬萊米） 600 g')
    expect(stapleAmount({ ...staple, tfda_name: null, display_amount: '1.50', display_unit: null })).toBe('白飯 1.5')
    expect(stapleAmount({ ...staple, display_amount: null })).toBeNull()
    expect(stapleAmount({ ...staple, display_amount: '' })).toBeNull()
  })

  it('主食份量算不出來的原因：只說明，不叫人重新生成（紀錄是唯讀的）', () => {
    expect(stapleProblem('staple_not_supported')).toContain('不在可選清單')
    expect(stapleProblem('no_tfda_cooked_sample')).toContain('沒有營養資料')
    expect(stapleProblem('staple_without_tfda_sample')).toContain('沒有營養資料')
    expect(stapleProblem(null)).toBe('無法換算成克數')
    expect(stapleProblem('staple_not_supported')).not.toContain('重新生成')
  })

  it('來源連結只接受 http／https', () => {
    expect(safeHref('https://example.org/a')).toBe('https://example.org/a')
    expect(safeHref('javascript:alert(1)')).toBeNull()
    expect(safeHref('不是網址')).toBeNull()
    expect(safeHref(null)).toBeNull()
  })
})
