import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { MealRequestSummary } from '../../api/types'
import { adoptedLabel, adoptedStatus, weekLabel, weekStatus } from './fridgeNoteText'
import { TicketListPanel } from './TicketListPanel'

/** 只看畫出來的文字（去掉標籤），確認使用者讀到什麼。 */
const text = (node: React.ReactElement) => renderToStaticMarkup(node).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')

const week = (over: object = {}) => ({
  status: 'ready' as const, meals: 6, energyReach: 0.957, averageCost: 181.6, unknownCostMeals: 0, ...over,
})

describe('便利貼「近 7 天」的說明', () => {
  it('三個數字都寫出來，成本標明是按比例成本', () => {
    expect(weekLabel(week())).toBe('近 7 天：採用 6 餐，熱量達成 96%，平均每餐按比例成本 NT$182。到冰箱門看營養與採購紀錄')
    expect(weekStatus(week())).toBe('6 餐')
  })

  it('未知寫「未知」，不寫 0', () => {
    const label = weekLabel(week({ energyReach: null, averageCost: null, unknownCostMeals: 6 }))
    expect(label).toContain('熱量達成 未知')
    expect(label).toContain('成本 未知，6 餐價格未知')
    expect(label).not.toContain('NT$0')
    expect(label).not.toContain('0%')
  })

  it('平均超過目標 110% 要寫出「超出」；剛好 110% 不算', () => {
    expect(weekLabel(week({ energyReach: 1.25 }))).toContain('熱量達成 125%，超出')
    expect(weekLabel(week({ energyReach: 1.1 }))).toContain('熱量達成 110%，平均')
    // 四捨五入後是 110% 但其實超過：不能寫 110% 又說超出
    expect(weekLabel(week({ energyReach: 1.104 }))).toContain('熱量達成 110.4%，超出')
  })

  it('沒有紀錄、讀取中、讀不到', () => {
    expect(weekLabel(week({ meals: 0, energyReach: null, averageCost: null }))).toContain('近 7 天沒有已採用的餐食')
    expect(weekLabel({ status: 'loading' })).toContain('讀取中')
    expect(weekLabel({ status: 'error' })).toContain('讀不到統計')
    expect(weekStatus({ status: 'loading' })).toBeUndefined()
    expect(weekStatus({ status: 'error' })).toBe('讀不到')
  })
})

describe('便利貼「最近採用」的說明', () => {
  it('日期與各餐的菜名', () => {
    const note = {
      status: 'ready' as const, date: '2026-11-14', linesError: false,
      lines: [{ meal: '午餐', dishes: '香煎鮭魚、清炒青江菜' }, { meal: '晚餐', dishes: '' }],
    }
    expect(adoptedLabel(note)).toBe('最近採用：11/14 週六，午餐 香煎鮭魚、清炒青江菜；晚餐 沒有菜名。到冰箱門看已採用紀錄')
    expect(adoptedStatus(note)).toBe('11/14')
  })

  it('菜色還沒讀到時只說日期', () => {
    expect(adoptedLabel({ status: 'ready', date: '2026-11-14', lines: null, linesError: false }))
      .toBe('最近採用：11/14 週六。到冰箱門看已採用紀錄')
  })

  it('還沒有紀錄、讀不到', () => {
    expect(adoptedLabel({ status: 'empty' })).toContain('還沒有採用過菜單')
    expect(adoptedStatus({ status: 'empty' })).toBe('還沒有紀錄')
    expect(adoptedLabel({ status: 'error' })).toContain('讀不到紀錄')
    expect(adoptedStatus({ status: 'loading' })).toBeUndefined()
  })
})

describe('TicketListPanel', () => {
  const noop = () => {}
  const base = {
    loading: false, busy: false, creating: false, onOpen: noop, onCreate: noop, onPick: noop, onClose: noop,
  }
  const requests: MealRequestSummary[] = [
    { id: 'a', status: 'running', version: 2, meal_date: '2026-11-15', meal_type: 'lunch', mode: 'questionnaire', updated_at: '' },
    { id: 'b', status: 'draft', version: 1, meal_date: null, meal_type: null, mode: 'picker', updated_at: '' },
    { id: 'c', status: 'confirmed', version: 4, meal_date: '2026-11-14', meal_type: 'dinner', mode: 'questionnaire', updated_at: '' },
  ]

  it('每張單寫出日期、餐別、版本與狀態（和舊前端的清單同樣的字）', () => {
    const out = text(<TicketListPanel {...base} requests={requests} householdName="林家廚房" memberNames={['阿明', '小惠']} />)
    expect(out).toContain('林家廚房，')
    expect(out).toContain('阿明、小惠')
    expect(out).toContain('11/15 週日 午餐 草稿第 2 版 生成中')
    expect(out).toContain('— 未選日期 未選餐別 食譜庫選菜 草稿')
    expect(out).toContain('11/14 週六 晚餐 草稿第 4 版 已採用')
    expect(out).toContain('新增問卷')
    expect(out).toContain('從食譜庫選菜')
  })

  it('沒有問卷時顯示引導，兩個入口都在', () => {
    const out = text(<TicketListPanel {...base} requests={[]} />)
    expect(out).toContain('規劃你家的第一餐')
    expect(out).toContain('新增問卷')
    expect(out).toContain('從食譜庫選菜')
  })

  it('開單中：按鈕換字，清單與兩個入口都先停用', () => {
    const html = renderToStaticMarkup(<TicketListPanel {...base} requests={requests} busy creating />)
    expect(html).toContain('開單中…')
    expect(html.match(/<button[^>]*disabled/g)?.length).toBe(requests.length + 2)
  })
})
