import { describe, expect, it } from 'vitest'
import { frameFor, isPhone } from '../../camera/framing'
import {
  COLUMN_Y, HANDLE, LEFT, MENU_CLEAR_PX, RIGHT, STOPS, TAB_Y, doorFit, mascotReach, worldX, worldY,
} from './layout'

const [adopted, nutrition] = STOPS

/** 畫稿上的 x 在畫面上的位置（px） */
function screenX(view: { w: number; h: number }, stop: typeof adopted, x: number): number {
  const frame = frameFor(view.w, view.h, stop)
  return frame.left + worldX(x) * frame.width
}
function screenY(view: { w: number; h: number }, stop: typeof adopted, y: number): number {
  const frame = frameFor(view.w, view.h, stop)
  return frame.top + worldY(y) * frame.height
}
const edges = (box: { x: number; width: number }) => [box.x - box.width / 2, box.x + box.width / 2] as const

describe('手機 390×844', () => {
  const view = { w: 390, h: 844 }

  it('是手機版面', () => {
    expect(isPhone(view.w, view.h)).toBe(true)
  })

  it('「已採用」：把手和左欄都完整入鏡', () => {
    const [handleLeft] = edges(HANDLE)
    const [columnLeft, columnRight] = edges(LEFT)
    expect(screenX(view, adopted, handleLeft)).toBeGreaterThanOrEqual(0)
    expect(screenX(view, adopted, columnRight)).toBeLessThanOrEqual(view.w)
    // 把手和左欄不重疊
    expect(screenX(view, adopted, HANDLE.x + HANDLE.width / 2)).toBeLessThan(screenX(view, adopted, columnLeft))
  })

  it('「已採用」：把手至少 44px 寬；月曆一格至少 44px 寬（便利貼左右內距共 17.5px）', () => {
    const handle = screenX(view, adopted, HANDLE.x + HANDLE.width / 2) - screenX(view, adopted, HANDLE.x - HANDLE.width / 2)
    const column = screenX(view, adopted, LEFT.x + LEFT.width / 2) - screenX(view, adopted, LEFT.x - LEFT.width / 2)
    expect(handle).toBeGreaterThanOrEqual(44)
    expect((column - 17.5) / 7).toBeGreaterThanOrEqual(44)
  })

  it('「採購」：右欄完整入鏡，左邊留給邊緣按鈕（約 66px）', () => {
    const [columnLeft, columnRight] = edges(RIGHT)
    expect(screenX(view, nutrition, columnLeft)).toBeGreaterThanOrEqual(66)
    expect(screenX(view, nutrition, columnRight)).toBeLessThanOrEqual(view.w)
  })

  it('磁鐵在停靠點指示（上方 30px）下面，欄在磁鐵（高 44px）下面', () => {
    for (const stop of STOPS) {
      const tab = screenY(view, stop, TAB_Y)
      expect(tab).toBeGreaterThanOrEqual(34)
      expect(screenY(view, stop, COLUMN_Y)).toBeGreaterThanOrEqual(tab + 44)
    }
  })
})

describe('桌機', () => {
  const views = [{ w: 1920, h: 1080 }, { w: 1440, h: 900 }, { w: 1366, h: 768 }, { w: 2560, h: 1080 }]

  it('兩顆磁鐵都在畫面內（16:9 的世界比畫面高，鏡頭要貼齊上緣）', () => {
    for (const view of views) {
      expect(isPhone(view.w, view.h)).toBe(false)
      expect(screenY(view, adopted, TAB_Y)).toBeGreaterThanOrEqual(8)
    }
  })

  it('把手不被左側欄（80px）蓋到，右欄不超出畫面', () => {
    for (const view of views) {
      expect(screenX(view, adopted, HANDLE.x - HANDLE.width / 2)).toBeGreaterThanOrEqual(80)
      expect(screenX(view, adopted, RIGHT.x + RIGHT.width / 2)).toBeLessThanOrEqual(view.w)
    }
  })
})

describe('吉祥物的頭頂', () => {
  it('跟著畫面寬度：跑道離底部 10px，圖高是寬的 7/6（寬 56–96px）', () => {
    expect(mascotReach(390)).toBeCloseTo(10 + 56 * 7 / 6, 5)
    expect(mascotReach(1440)).toBeCloseTo(10 + 93.6 * 7 / 6, 5)
    expect(mascotReach(2560)).toBeCloseTo(10 + 96 * 7 / 6, 5)
  })
})

describe('磁鐵與欄的讓位（doorFit）', () => {
  // 手機底部：分頁 56px；上面一排物件列（Room 的 DOCK_LANE_PX：連間距 64px，按鈕那一條高 56、離分頁 12）
  const TABS = 56
  const DOCK_LANE = 64
  /** 樣式算出來的欄外框下緣（px，從畫面上緣算）：room 再扣掉分頁、物件列與底部安全區 */
  const columnBottom = (columnTop: number, room: number, safeBottom: number) => columnTop + room - (TABS + DOCK_LANE) - safeBottom

  // 844＝設計基準；664＝同一支手機在 Safari 裡（工具列佔掉 180px）；667、640＝較小的手機
  const phones = [{ w: 390, h: 844 }, { w: 390, h: 664 }, { w: 375, h: 667 }, { w: 360, h: 640 }]

  it('畫面夠高時不用讓（390×844 和原本的位置一樣）', () => {
    const view = phones[0]
    for (const stop of STOPS) {
      expect(doorFit(view, frameFor(view.w, view.h, stop), true).push).toBe(0)
    }
  })

  it('畫面矮的手機：磁鐵排到家庭選單下面，欄在磁鐵（高 44px）下面', () => {
    for (const view of phones) {
      expect(isPhone(view.w, view.h)).toBe(true)
      for (const stop of STOPS) {
        const { push } = doorFit(view, frameFor(view.w, view.h, stop), true)
        const tab = screenY(view, stop, TAB_Y) + push
        // push 取到 0.1px
        expect(tab).toBeGreaterThanOrEqual(MENU_CLEAR_PX - 0.05)
        expect(screenY(view, stop, COLUMN_Y) + push).toBeGreaterThanOrEqual(tab + 44)
      }
    }
    // 390×664：世界貼齊畫面上緣，磁鐵原本在 22px 高的地方，被選單（12–56）蓋住
    const short = phones[1]
    expect(screenY(short, adopted, TAB_Y)).toBeLessThan(MENU_CLEAR_PX)
    expect(doorFit(short, frameFor(short.w, short.h, adopted), true).push).toBeGreaterThan(30)
  })

  it('欄的下緣停在吉祥物頭頂，不會排進跑道、物件列或分頁（有沒有底部安全區都一樣）', () => {
    for (const view of phones) {
      for (const stop of STOPS) {
        for (const safeBottom of [0, 34]) {
          const { push, room } = doorFit(view, frameFor(view.w, view.h, stop), true)
          const top = screenY(view, stop, COLUMN_Y) + push
          const bottom = columnBottom(top, room, safeBottom)
          // 吉祥物的跑道在物件列上面（Mascot.module.css 的 --lane-bottom）
          const mascotTop = view.h - (TABS + DOCK_LANE) - safeBottom - mascotReach(view.w)
          const dockTop = view.h - TABS - safeBottom - 12 - 56
          expect(bottom).toBeCloseTo(mascotTop, 0)
          expect(bottom).toBeLessThan(dockTop)
          // 還要留得下夠捲動的高度（至少五列 44px 的格子）
          expect(bottom - top).toBeGreaterThan(220)
        }
      }
    }
  })

  it('桌機不讓位，欄可以用到畫面底部（和原本一樣）', () => {
    const view = { w: 1440, h: 900 }
    const frame = frameFor(view.w, view.h, adopted)
    const { push, room } = doorFit(view, frame, false)
    expect(push).toBe(0)
    expect(screenY(view, adopted, COLUMN_Y) + room).toBeCloseTo(view.h, 0)
  })

  it('視窗矮到放不下時是 0，不會變成負的', () => {
    const view = { w: 1440, h: 80 }
    expect(doorFit(view, frameFor(view.w, view.h, adopted), false).room).toBe(0)
  })
})
