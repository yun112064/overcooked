import { describe, expect, it } from 'vitest'
import { ACTION_BAR_PX, HUD_TABS, RAIL_PX, TAB_BAR_PX, WIDE_ACTION_PX, hudInsets } from './hudLayout'

describe('HUD_TABS', () => {
  it('五個分頁依設計的順序，各自對應一個路由', () => {
    expect(HUD_TABS.map((t) => [t.label, t.to])).toEqual([
      ['今日', '/today'], ['規劃', '/plan'], ['食譜庫', '/pick'], ['紀錄', '/records'], ['家庭', '/family'],
    ])
  })
})

describe('hudInsets', () => {
  it('手機：有分頁時底部讓出分頁的高度', () => {
    expect(hudInsets(true, true, false)).toEqual({ bottom: TAB_BAR_PX, left: 0 })
  })

  it('手機：動作列取代分頁，只算動作列', () => {
    expect(hudInsets(true, true, true)).toEqual({ bottom: ACTION_BAR_PX, left: 0 })
    expect(hudInsets(true, false, true)).toEqual({ bottom: ACTION_BAR_PX, left: 0 })
  })

  it('手機：沒有導覽也沒有動作列就不佔', () => {
    expect(hudInsets(true, false, false)).toEqual({ bottom: 0, left: 0 })
  })

  it('桌機：分頁變成左側欄，不佔底部', () => {
    expect(hudInsets(false, true, false)).toEqual({ bottom: 0, left: RAIL_PX })
  })

  it('桌機：動作列浮在底部，側欄照樣在', () => {
    expect(hudInsets(false, true, true)).toEqual({ bottom: WIDE_ACTION_PX, left: RAIL_PX })
    expect(hudInsets(false, false, true)).toEqual({ bottom: WIDE_ACTION_PX, left: 0 })
  })
})
