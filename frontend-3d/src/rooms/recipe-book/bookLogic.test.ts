import { describe, expect, it } from 'vitest'
import type { CatalogDish, Member, StapleOptions } from '../../api/types'
import {
  MAX_DISHES, NO_FILTERS, bookmarkValues, canToggle, chosenNames, clampPage, countPicks, dishTags, filterCount,
  filterDishes, hasStapleDish, initialPicks, initialStep, mealByClock, pageCount, pageOf, pickerDraft, savedSelections,
  stapleFor, toggleValue,
} from './bookLogic'

function dish(patch: Partial<CatalogDish> & { dish_name: string }): CatalogDish {
  return {
    standard_dish_id: `D-${patch.dish_name}`, cuisine: '台式', category: '主菜', staple_kind: null, meal_type: '正餐',
    main_ingredients: [], ingredients: [], conflicts: [], ...patch,
  }
}

const member = (id: string): Member => ({
  id, display_name: id, birth_date: '1990-01-01', age_years: 36, equation_sex: 'F', weight_kg: '55',
  activity_category: 'low_active', goal: 'general', portion: '1', allergens: [], avoided: [], version: 1, updated_at: '',
})

const STAPLES: StapleOptions = {
  options: [{ label: '白飯', aliases: [] }, { label: '糙米飯', aliases: [] }],
  none_value: '不需要主食', none_values: ['不需要主食'], config_version: null,
}

describe('mealByClock', () => {
  it('10 點前早餐、15 點前午餐、之後晚餐', () => {
    expect(mealByClock(new Date(2026, 10, 14, 9, 59))).toBe('breakfast')
    expect(mealByClock(new Date(2026, 10, 14, 10, 0))).toBe('lunch')
    expect(mealByClock(new Date(2026, 10, 14, 14, 59))).toBe('lunch')
    expect(mealByClock(new Date(2026, 10, 14, 15, 0))).toBe('dinner')
  })
})

describe('initialPicks / countPicks', () => {
  it('照已存的單勾選；沒存過的成員都不勾；不在成員清單裡的人不帶進來', () => {
    const picks = initialPicks([member('a'), member('b')], [
      { member_id: 'a', dining: true, bento: true, bento_meal: 'lunch' },
      { member_id: 'gone', dining: true, bento: false, bento_meal: 'lunch' },
    ])
    expect(picks).toEqual({ a: { dining: true, bento: true }, b: { dining: false, bento: false } })
    expect(countPicks(picks)).toEqual({ dining: 1, bento: 1 })
  })
})

describe('savedSelections', () => {
  const afternoon = new Date(2026, 10, 14, 16, 0)

  it('新的單：餐別照現在時間，沒有人被勾、托盤是空的', () => {
    expect(savedSelections([member('a')], null, afternoon)).toEqual({
      mealType: 'dinner', picks: { a: { dining: false, bento: false } }, picked: [],
    })
  })

  it('已存的單（版本衝突後重新讀取也走這裡）：餐別、成員、挑的菜全部照伺服器那一份，不留畫面上原本的選擇', () => {
    const saved = savedSelections([member('a'), member('b')], {
      meal_type: 'breakfast',
      participants: [{ member_id: 'b', dining: true, bento: true, bento_meal: 'lunch' }],
      specified_dishes: ['番茄炒蛋', '滷肉'],
    }, afternoon)
    expect(saved).toEqual({
      mealType: 'breakfast',
      picks: { a: { dining: false, bento: false }, b: { dining: true, bento: true } },
      picked: ['番茄炒蛋', '滷肉'],
    })
  })

  it('挑的菜是複本：之後在畫面上增減不會改到快取裡的那一份', () => {
    const draft = { meal_type: null, participants: [], specified_dishes: ['滷肉'] }
    const saved = savedSelections([], draft, afternoon)
    saved.picked.push('炒青菜')
    expect(draft.specified_dishes).toEqual(['滷肉'])
  })
})

describe('pickerDraft', () => {
  const picks = { a: { dining: true, bento: false }, b: { dining: false, bento: true }, c: { dining: false, bento: false } }

  it('新的單：今天、沒有預算口味與已有食材，只帶有勾的人，便當算午餐', () => {
    expect(pickerDraft('dinner', picks, undefined, '2026-11-14')).toEqual({
      mode: 'picker', meal_date: '2026-11-14', meal_type: 'dinner', budget: null, staple: null, dish_count: null,
      taste_preferences: '', specified_dishes: [], available_ingredient_ids: [],
      participants: [
        { member_id: 'a', dining: true, bento: false, bento_meal: 'lunch' },
        { member_id: 'b', dining: false, bento: true, bento_meal: 'lunch' },
      ],
    })
  })

  it('已存的單：日期、主食、道數、已挑的菜沿用；預算口味食材仍然清空', () => {
    const base = {
      ...pickerDraft('lunch', picks, undefined, '2026-11-01'),
      staple: '白飯', dish_count: 2, specified_dishes: ['番茄炒蛋', '香菇雞湯'],
      budget: '500', taste_preferences: '清淡', available_ingredient_ids: ['FOOD1'],
    }
    const draft = pickerDraft('dinner', picks, base, '2026-11-14')
    expect(draft.meal_date).toBe('2026-11-01')
    expect(draft.meal_type).toBe('dinner')
    expect(draft.staple).toBe('白飯')
    expect(draft.dish_count).toBe(2)
    expect(draft.specified_dishes).toEqual(['番茄炒蛋', '香菇雞湯'])
    expect(draft.budget).toBeNull()
    expect(draft.taste_preferences).toBe('')
    expect(draft.available_ingredient_ids).toEqual([])
  })
})

describe('initialStep', () => {
  it('新的單從第一步；不是草稿直接看結果；草稿照上一頁指定的步驟', () => {
    expect(initialStep(null, { step: 1 })).toBe(0)
    expect(initialStep({ status: 'queued' }, null)).toBe(2)
    expect(initialStep({ status: 'confirmed' }, { step: 1 })).toBe(2)
    expect(initialStep({ status: 'draft' }, null)).toBe(0)
    expect(initialStep({ status: 'draft' }, { step: 1 })).toBe(1)
  })

  it('上一頁帶來的不是 1 或 2 就從頭', () => {
    expect(initialStep({ status: 'draft' }, { step: 7 })).toBe(0)
    expect(initialStep({ status: 'draft' }, { step: '1' })).toBe(0)
    expect(initialStep({ status: 'draft' }, 'x')).toBe(0)
  })
})

describe('filterDishes', () => {
  const dishes = [
    dish({ dish_name: '蔥油雞腿', category: '主菜', cuisine: '台式', main_ingredients: ['雞腿肉'], ingredients: ['雞腿肉', '青蔥'] }),
    dish({ dish_name: '味噌豆腐湯', category: '湯', cuisine: '日式', main_ingredients: ['嫩豆腐'], ingredients: ['嫩豆腐', '味噌', '青蔥'] }),
    dish({ dish_name: '咖哩飯', category: '主食', cuisine: '日式', staple_kind: '飯', main_ingredients: ['雞腿肉', '馬鈴薯'], ingredients: ['白飯', '咖哩塊'] }),
  ]
  const names = (list: CatalogDish[]) => list.map((d) => d.dish_name)

  it('沒有條件時全部都在', () => {
    expect(filterDishes(dishes, NO_FILTERS, '  ')).toHaveLength(3)
  })

  it('同一個分類裡符合任一個就算', () => {
    expect(names(filterDishes(dishes, { ...NO_FILTERS, category: ['湯', '主食'] }, ''))).toEqual(['味噌豆腐湯', '咖哩飯'])
  })

  it('不同分類要同時符合', () => {
    expect(names(filterDishes(dishes, { ...NO_FILTERS, cuisine: ['日式'], main_ingredient: ['雞腿肉'] }, ''))).toEqual(['咖哩飯'])
    expect(filterDishes(dishes, { ...NO_FILTERS, cuisine: ['台式'], category: ['湯'] }, '')).toEqual([])
  })

  it('沒有米飯麵種類的菜，選了米飯麵就不會出現', () => {
    expect(names(filterDishes(dishes, { ...NO_FILTERS, staple_kind: ['飯'] }, ''))).toEqual(['咖哩飯'])
  })

  it('關鍵字比對菜名或食材，前後空白不算', () => {
    expect(names(filterDishes(dishes, NO_FILTERS, ' 青蔥 '))).toEqual(['蔥油雞腿', '味噌豆腐湯'])
    expect(names(filterDishes(dishes, NO_FILTERS, '咖哩'))).toEqual(['咖哩飯'])
    expect(names(filterDishes(dishes, { ...NO_FILTERS, category: ['主菜'] }, '青蔥'))).toEqual(['蔥油雞腿'])
  })
})

describe('挑菜', () => {
  it('toggleValue 加入或拿掉，順序照加入的先後', () => {
    expect(toggleValue(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleValue(['a', 'b'], 'a')).toEqual(['b'])
  })

  it('filterCount 是四種分類勾選的總數', () => {
    expect(filterCount(NO_FILTERS)).toBe(0)
    expect(filterCount({ main_ingredient: ['a'], cuisine: [], category: ['b', 'c'], staple_kind: [] })).toBe(3)
  })

  it('食譜庫沒有的菜名不算已選', () => {
    const byName = new Map([['番茄炒蛋', dish({ dish_name: '番茄炒蛋' })]])
    expect(chosenNames(['阿嬤的滷肉', '番茄炒蛋'], byName)).toEqual(['番茄炒蛋'])
  })

  it('含禁忌的菜不能加；滿了不能再加；已經選的一定拿得出來', () => {
    const ok = dish({ dish_name: '番茄炒蛋' })
    const blocked = dish({ dish_name: '蒜蓉蒸蝦', conflicts: ['白蝦'] })
    expect(canToggle(ok, false, 0)).toBe(true)
    expect(canToggle(ok, false, MAX_DISHES)).toBe(false)
    expect(canToggle(ok, true, MAX_DISHES)).toBe(true)
    expect(canToggle(blocked, false, 0)).toBe(false)
    expect(canToggle(blocked, true, 3)).toBe(true)
  })

  it('標籤：菜系・類型・米飯麵・主要食材，早餐多一個「早餐」', () => {
    expect(dishTags(dish({ dish_name: '咖哩飯', cuisine: '日式', category: '主食', staple_kind: '飯', main_ingredients: ['雞腿肉'] })))
      .toEqual(['日式', '主食', '飯', '雞腿肉'])
    expect(dishTags(dish({ dish_name: '蛋餅', meal_type: '早餐' }))).toEqual(['台式', '主菜', '早餐'])
  })

  it('挑到主食類就不另配主食；沒挑就配清單第一個', () => {
    const byName = new Map([
      ['咖哩飯', dish({ dish_name: '咖哩飯', category: '主食' })],
      ['番茄炒蛋', dish({ dish_name: '番茄炒蛋' })],
    ])
    expect(hasStapleDish(['番茄炒蛋'], byName)).toBe(false)
    expect(hasStapleDish(['番茄炒蛋', '咖哩飯'], byName)).toBe(true)
    expect(stapleFor(false, STAPLES)).toBe('白飯')
    expect(stapleFor(true, STAPLES)).toBe('不需要主食')
    expect(stapleFor(false, { ...STAPLES, options: [] })).toBe('不需要主食')
  })
})

describe('分頁', () => {
  const items = Array.from({ length: 47 }, (_, i) => i)

  it('一頁 10 道；沒有菜也算一頁', () => {
    expect(pageCount(47)).toBe(5)
    expect(pageCount(10)).toBe(1)
    expect(pageCount(0)).toBe(1)
  })

  it('頁碼超出範圍時夾回來', () => {
    expect(clampPage(9, 47)).toBe(4)
    expect(clampPage(-1, 47)).toBe(0)
    expect(clampPage(3, 0)).toBe(0)
  })

  it('pageOf 取出那一頁；篩選後頁數變少時給最後一頁', () => {
    expect(pageOf(items, 0)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    expect(pageOf(items, 4)).toEqual([40, 41, 42, 43, 44, 45, 46])
    expect(pageOf(items.slice(0, 12), 4)).toEqual([10, 11])
  })

  it('書籤最多放四個類型', () => {
    const facets = ['主菜', '湯', '主食', '點心', '飲品'].map((value) => ({ value, count: 1 }))
    expect(bookmarkValues(facets)).toEqual(['主菜', '湯', '主食', '點心'])
    expect(bookmarkValues(facets.slice(0, 2))).toEqual(['主菜', '湯'])
  })
})
