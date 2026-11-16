import { describe, expect, it } from 'vitest'
import type { KnowledgeTips } from '../../api/types'
import {
  BAR_NOTES, TIP_UNAVAILABLE, amountText, barTone, clock, feedbackWeightText, isActiveJob, nutrientCell, pollInterval, reference, retrievalMessage, safeHref,
  shareText, sourceName, targetNote, tipSites,
} from './previewText'

describe('retrievalMessage', () => {
  it('有菜名時直接寫出來', () => {
    expect(retrievalMessage({ reason: 'SPECIFIED_DISH_DIETARY_CONFLICT', details: { specified_dishes: ['蒜蓉蒸蝦'] } }, null))
      .toContain('指定的「蒜蓉蒸蝦」含有成員過敏或禁吃的食材')
    expect(retrievalMessage({ reason: 'SPECIFIED_DISH_STAPLE_CONFLICT', details: { specified_dishes: ['咖哩飯'] } }, '白飯'))
      .toContain('和你選的主食「白飯」重複')
  })

  it('沒有菜名時用「菜」；不認得的原因給一般說明', () => {
    expect(retrievalMessage({ reason: 'SPECIFIED_DISH_NOT_IN_DATABASE' }, null)).toContain('找不到指定的菜')
    expect(retrievalMessage({ reason: 'SOMETHING_NEW' }, null)).toBe('資料庫沒有可用的菜單，可以調整問卷後再試。')
    expect(retrievalMessage({ reason: null }, null)).toBe('資料庫沒有可用的菜單，可以調整問卷後再試。')
  })
})

describe('小知識的來源', () => {
  it('兩行以上取第二行（去掉後面的卷期頁碼），一行就用那一行，沒有就用網址的主機名', () => {
    expect(sourceName('標題\n健康醫療網 2024', 'https://example.com/a')).toBe('健康醫療網')
    expect(sourceName('Hello醫師', 'https://example.com/a')).toBe('Hello醫師')
    expect(sourceName(null, 'https://www.example.com/a')).toBe('www.example.com')
    expect(sourceName(undefined, '不是網址')).toBe('原文')
  })

  it('只有 http(s) 的網址可以當連結', () => {
    expect(safeHref('https://example.com/a')).toBe('https://example.com/a')
    expect(safeHref('javascript:alert(1)')).toBeNull()
    expect(safeHref('不是網址')).toBeNull()
    expect(safeHref(null)).toBeNull()
  })

  it('tipSites 列出用到的網站並去重；拿不到時是空的', () => {
    const tips: KnowledgeTips = {
      status: 'available', dropped: [], not_found: [], knowledge_status: null, nutrition_status: null,
      items: [
        { ingredient_id: 'a', ingredient_name: 'A', tip_zh: '…', citations: [
          { knowledge_id: '1', source_url: 'https://x.example', source_label: '健康醫療網', conditions: null }] },
        { ingredient_id: 'b', ingredient_name: 'B', tip_zh: '…', citations: [
          { knowledge_id: '2', source_url: 'https://x.example', source_label: '健康醫療網', conditions: null },
          { knowledge_id: '3', source_url: 'https://y.example', source_label: 'Hello醫師', conditions: null }] },
      ],
    }
    expect(tipSites(tips)).toBe('健康醫療網、Hello醫師')
    expect(tipSites({ ...tips, status: 'unavailable' })).toBe('')
    expect(tipSites(undefined)).toBe('')
  })
})

describe('targetNote', () => {
  it('熱量：不超過 110% 是好的，超過就提醒', () => {
    expect(targetNote('energy_kcal', 2100, 2000, 'kcal')).toEqual({ note: '目標 2000 kcal・+5%', tone: 'good' })
    expect(targetNote('energy_kcal', 2300, 2000, 'kcal')).toEqual({ note: '目標 2000 kcal・+15%', tone: 'warn' })
    expect(targetNote('energy_kcal', 1000, 2000, 'kcal').tone).toBe('good')
  })

  it('蛋白質：不低於 90% 是好的', () => {
    expect(targetNote('protein_g', 85, 100, 'g')).toEqual({ note: '目標 100 g・-15%', tone: 'warn' })
    expect(targetNote('protein_g', 150, 100, 'g').tone).toBe('good')
  })

  it('值未知時只寫目標、不寫百分比；目標也未知就什麼都不寫', () => {
    expect(targetNote('energy_kcal', null, 2000, 'kcal')).toEqual({ note: '目標 2000 kcal', tone: 'default' })
    expect(targetNote('energy_kcal', 1800, null, 'kcal')).toEqual({ note: null, tone: 'default' })
  })
})

describe('nutrientCell', () => {
  const all = {
    totals: { energy_kcal: '1820.4', protein_g: '88.25', sugars_g: null, dietary_fiber_g: null, sodium_mg: '0' },
    known_partial: { dietary_fiber_g: '12.34' },
    missing_by_nutrient: { dietary_fiber_g: ['海帶芽'], sugars_g: ['糙米飯', '味噌'] },
  }

  it('有值：kcal、mg 到整數，g 到 0.1', () => {
    expect(nutrientCell(all, 'energy_kcal', 'kcal')).toEqual({ value: '1820 kcal', detail: null })
    expect(nutrientCell(all, 'protein_g', 'g')).toEqual({ value: '88.3 g', detail: null })
  })

  it('真的是 0 就顯示 0，不當成未知', () => {
    expect(nutrientCell(all, 'sodium_mg', 'mg')).toEqual({ value: '0 mg', detail: null })
  })

  it('缺值：不給數字，只說已知多少、缺哪些食材', () => {
    expect(nutrientCell(all, 'dietary_fiber_g', 'g')).toEqual({ value: null, detail: '已知 12.3 g，缺：海帶芽' })
    expect(nutrientCell(all, 'sugars_g', 'g')).toEqual({ value: null, detail: '缺：糙米飯、味噌' })
    expect(nutrientCell(all, 'fat_g', 'g')).toEqual({ value: null, detail: null })
  })
})

describe('每人營養', () => {
  it('amountText：kcal、mg 取整，其餘到 0.1', () => {
    expect(amountText(612.6, 'kcal')).toBe('613')
    expect(amountText(12.34, 'g')).toBe('12.3')
  })

  it('reference：範圍、上限、建議值', () => {
    expect(reference({ kind: 'range', min: '250', max: '300', label: '' }, 'g')).toEqual({ text: '250–300 g', base: 300, low: 250, high: 300 })
    expect(reference({ kind: 'max', max: '2400', label: '' }, 'mg')).toEqual({ text: '< 2400 mg', base: 2400, low: null, high: 2400 })
    expect(reference({ kind: 'target', value: '2000', label: '' }, 'kcal')).toEqual({ text: '2000 kcal', base: 2000, low: 2000, high: 2000 })
    expect(reference({ kind: 'target', label: '' }, 'kcal')).toBeNull()
    expect(reference(undefined, 'kcal')).toBeNull()
  })

  it('shareText：範圍給兩個百分比，其餘給一個', () => {
    expect(shareText(75, { text: '', base: 300, low: 250, high: 300 })).toBe('25–30%')
    expect(shareText(600, { text: '', base: 2000, low: 2000, high: 2000 })).toBe('30%')
    expect(shareText(1200, { text: '', base: 2400, low: null, high: 2400 })).toBe('50%')
  })

  it('barTone：只有上限類會變色', () => {
    expect(barTone('target', 1.5, 0.3)).toBe('plain')
    expect(barTone('max', 0.2, 0.3)).toBe('plain')
    expect(barTone('max', 0.5, 0.3)).toBe('overMeal')
    expect(barTone('max', 1.2, 0.3)).toBe('overDay')
  })

  it('BAR_NOTES：變色的長條都有配的字（和餐桌、冰箱同一句），一般的沒有', () => {
    expect(BAR_NOTES[barTone('max', 0.5, 0.3)]).toBe('超過這一餐的份額')
    expect(BAR_NOTES[barTone('max', 1.2, 0.3)]).toBe('超過每日上限')
    expect(BAR_NOTES[barTone('max', 0.2, 0.3)]).toBeNull()
  })
})

describe('pollInterval', () => {
  it('工作在排隊或執行中：每 2 秒問一次；結束了就不問', () => {
    expect(isActiveJob({ status: 'queued' })).toBe(true)
    expect(pollInterval({ status: 'queued' }, false)).toBe(2000)
    expect(pollInterval({ status: 'running' }, true)).toBe(2000)
    expect(pollInterval({ status: 'succeeded' }, false)).toBe(false)
    expect(pollInterval({ status: 'failed' }, true)).toBe(false)
  })

  it('一次都還沒讀到（第一次讀取失敗）而這張單寫著生成中：繼續問，不然進度、取消和鎖都卡住', () => {
    expect(pollInterval(undefined, true)).toBe(2000)
    expect(pollInterval(undefined, false)).toBe(false)
  })

  it('讀到了、這張單沒有生成工作（null）：不問', () => {
    expect(isActiveJob(null)).toBe(false)
    expect(pollInterval(null, true)).toBe(false)
    expect(pollInterval(null, false)).toBe(false)
  })
})

describe('clock', () => {
  it('秒數寫成 分:秒', () => {
    expect(clock(0)).toBe('0:00')
    expect(clock(95)).toBe('1:35')
  })
})

describe('feedbackWeightText', () => {
  it('DEMO 工作簿重量不寫成整份配方烹調前', () => {
    const text = feedbackWeightText({ whole_recipe_pre_cooking_grams: '120', weight_semantics: 'workbook_weight_g_unverified' })
    expect(text).toBe('工作簿重量（未核對是否為整份配方烹調前） 120 g')
    expect(feedbackWeightText({ whole_recipe_pre_cooking_grams: null })).toBe('整份配方烹調前 未知 g')
  })
})

describe('TIP_UNAVAILABLE', () => {
  it('DEMO 後端的雲端向量化未設定有自己的說明', () => {
    expect(TIP_UNAVAILABLE.EMBEDDING_NOT_CONFIGURED).toContain('OPENAI_API_KEY')
    expect(TIP_UNAVAILABLE.ADOPTION_BLOCKED).toBeTruthy()
  })
})
