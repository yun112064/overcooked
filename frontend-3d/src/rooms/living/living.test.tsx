import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { TodayCard, TodayTable } from './TodayCard'
import { TvScreen } from './Tv'
import { dayBars, type TodayPlan, type TrendItem } from './today'

const TODAY = '2026-11-14'
const noop = () => {}
/** 只看畫出來的文字（去掉標籤） */
const text = (markup: string) => markup.replace(/<[^>]+>/g, '')

function item(date: string, energy: TrendItem['energy_kcal'], target: TrendItem['target_energy_kcal']): TrendItem {
  return { meal_date: date, meal_type: 'dinner', energy_kcal: energy, protein_g: null, target_energy_kcal: target, target_protein_g: null }
}

function tv(items: TrendItem[]) {
  return renderToStaticMarkup(
    <TvScreen meal="dinner" onMeal={noop} trend={{ status: 'ready', bars: dayBars(items, 'dinner', TODAY) }} />,
  )
}

describe('TvScreen', () => {
  it('三顆餐別按鈕是真的 button，選中的那一顆標 aria-pressed', () => {
    const markup = tv([])
    expect(markup.match(/<button/g)).toHaveLength(3)
    expect(markup).toContain('<button type="button" aria-pressed="true"><span>晚餐</span></button>')
    expect(markup).toContain('<button type="button" aria-pressed="false"><span>早餐</span></button>')
  })

  it('已知的日子畫出有高度的長條；目標線在 100%', () => {
    const markup = tv([item(TODAY, '650', '650')])
    expect(markup).toContain('height:80%') // 100% ÷ 圖表上緣 125%
    expect(markup).toContain('bottom:80%')
    expect(text(markup)).toContain('今天 100%')
    expect(text(markup)).toContain('11/14（六）：熱量 650 kcal，目標 650 kcal，達成 100%，通過')
  })

  it('未知的日子不畫成有高度的長條，也不寫成 0', () => {
    const markup = tv([item(TODAY, null, '650'), item('2026-11-13', '624', '650')])
    // 只有 11/13 那一根有高度
    expect(markup.match(/height:/g)).toHaveLength(1)
    expect(text(markup)).toContain('今天 未知')
    expect(text(markup)).toContain('11/14（六）：熱量未知，計算不完整')
    expect(text(markup)).not.toMatch(/(^|\D)0%/)
    expect(text(markup)).not.toMatch(/熱量 0 kcal/)
  })

  it('沒有紀錄的日子什麼都不畫，文字寫沒有採用紀錄', () => {
    const markup = tv([item('2026-11-13', '650', '650')])
    expect(markup.match(/height:/g)).toHaveLength(1)
    expect(text(markup)).toContain('今天沒有紀錄')
    expect(text(markup)).toContain('11/14（六）：沒有採用紀錄')
  })

  it('超過 110% 除了換顏色還標「!」', () => {
    const markup = tv([item(TODAY, '780', '650')])
    expect(markup).toMatch(/height:96%">!<\/span>/) // 120% ÷ 125%
    expect(text(markup)).toContain('達成 120%，超出')
  })

  it('七天都沒有這個餐別：用文字說明，不畫空的圖表', () => {
    const markup = tv([])
    expect(text(markup)).toContain('近 7 天沒有採用的晚餐')
    expect(markup).not.toContain('height:')
  })

  it('讀取中與讀取失敗都有文字，按鈕照常可以切換', () => {
    const loading = renderToStaticMarkup(<TvScreen meal="lunch" onMeal={noop} trend={{ status: 'loading' }} />)
    const failed = renderToStaticMarkup(<TvScreen meal="lunch" onMeal={noop} trend={{ status: 'error' }} />)
    expect(text(loading)).toContain('讀取中…')
    expect(text(failed)).toContain('讀不到紀錄')
    expect(failed.match(/<button/g)).toHaveLength(3)
  })
})

const plan: TodayPlan = {
  id: 'p1', meal_date: TODAY, meal_type: 'dinner', adopted_at: '2026-11-14T09:00:00Z',
  dishes: [{ dish_name: '地瓜糙米飯' }, { dish_name: '清蒸鱸魚' }, { dish_name: '蒜炒高麗菜' }],
  nutrition: { energy_kcal: '624', protein_g: '31.2', target: { energy_kcal: '650', protein_g: '30' } },
}

function card(state: Parameters<typeof TodayCard>[0]['state']) {
  return renderToStaticMarkup(<TodayCard meal="dinner" today={TODAY} state={state} />)
}

describe('TodayCard', () => {
  it('有採用的餐食：菜名、兩顆營養徽章與整餐狀態', () => {
    const out = text(card({ status: 'ready', plan, others: 0 }))
    expect(out).toContain('今天的晚餐')
    expect(out).toContain('11/14（六）')
    expect(out).toContain('已採用')
    expect(out).toContain('地瓜糙米飯、清蒸鱸魚、蒜炒高麗菜')
    expect(out).toContain('熱量96%通過')
    expect(out).toContain('蛋白質104%通過')
    expect(out).toContain('整餐 通過')
  })

  it('熱量超出：徽章與整餐狀態都寫出來', () => {
    const over: TodayPlan = { ...plan, nutrition: { energy_kcal: '780', protein_g: '31.2', target: { energy_kcal: '650', protein_g: '30' } } }
    const out = text(card({ status: 'ready', plan: over, others: 0 }))
    expect(out).toContain('熱量120%超出')
    expect(out).toContain('整餐 超出或不足')
  })

  it('熱量未知：寫計算不完整，不顯示 0%', () => {
    const unknown: TodayPlan = { ...plan, nutrition: { energy_kcal: null, protein_g: '31.2', target: { energy_kcal: '650', protein_g: '30' } } }
    const out = text(card({ status: 'ready', plan: unknown, others: 0 }))
    expect(out).toContain('熱量計算不完整')
    expect(out).toContain('整餐 計算不完整')
    expect(out).not.toContain('0%')
  })

  it('還沒有採用：提示去廚房，沒有任何營養數字', () => {
    const out = text(card({ status: 'ready', plan: null, others: 0 }))
    expect(out).toContain('還沒決定')
    expect(out).toContain('到廚房規劃一餐')
    expect(out).not.toContain('%')
  })

  it('同一餐別有好幾筆時說明顯示的是哪一筆', () => {
    expect(text(card({ status: 'ready', plan, others: 2 }))).toContain('另有 2 筆採用紀錄')
  })

  it('讀取中與讀取失敗都有文字', () => {
    expect(text(card({ status: 'loading' }))).toContain('正在看今天吃什麼…')
    expect(text(card({ status: 'error' }))).toContain('讀不到今天的餐單。')
  })

  it('小卡與盤子都不能點', () => {
    const markup = renderToStaticMarkup(<TodayTable meal="dinner" today={TODAY} state={{ status: 'ready', plan, others: 0 }} />)
    expect(markup).not.toMatch(/<button|<a /)
  })
})

describe('TodayTable', () => {
  const food = (state: Parameters<typeof TodayCard>[0]['state']) =>
    renderToStaticMarkup(<TodayTable meal="dinner" today={TODAY} state={state} />).includes('<img')

  it('有採用的餐食才在盤子上擺菜；沒有、讀取中、讀不到都是空盤', () => {
    expect(food({ status: 'ready', plan, others: 0 })).toBe(true)
    expect(food({ status: 'ready', plan: null, others: 0 })).toBe(false)
    expect(food({ status: 'loading' })).toBe(false)
    expect(food({ status: 'error' })).toBe(false)
  })
})
