import { describe, expect, it } from 'vitest'
import { LAYER_BLEED, WORLD_W, frameFor, isPhone } from '../../camera/framing'
import { LEFT_PAGE, RIGHT_PAGE, TABS, TOP_Y, TRAY, bookLayout, visibleBand } from './bookFraming'

const LABELS = ['清單', '細節'] as const

/** 畫稿的 x 在畫面上的位置（px），和 framing.place 同一個換算。 */
function screenX(viewW: number, viewH: number, stopIndex: 0 | 1, x: number): number {
  const stop = bookLayout(viewW, viewH, LABELS).stops[stopIndex]
  const frame = frameFor(viewW, viewH, stop)
  return frame.left + frame.width * (0.5 + (x / WORLD_W - 0.5) * LAYER_BLEED)
}

/** 畫稿的 y 離畫面上緣幾 px。 */
function screenY(viewW: number, viewH: number, y: number): number {
  const band = visibleBand(viewW, viewH, bookLayout(viewW, viewH, LABELS).stops[0])
  return (y - band.top) * band.unitPx
}

describe('手機（390×844）', () => {
  const [w, h] = [390, 844]

  it('清單那一站：整張左頁和上方的標籤都在畫面裡', () => {
    expect(isPhone(w, h)).toBe(true)
    expect(screenX(w, h, 0, LEFT_PAGE.x - LEFT_PAGE.width / 2)).toBeGreaterThanOrEqual(0)
    expect(screenX(w, h, 0, LEFT_PAGE.x + LEFT_PAGE.width / 2)).toBeLessThanOrEqual(w)
    // 闔上書（205–395）與步驟（400–780）
    expect(screenX(w, h, 0, 205)).toBeGreaterThanOrEqual(0)
    expect(screenX(w, h, 0, 780)).toBeLessThanOrEqual(w)
  })

  it('細節那一站：整張右頁和整個托盤（外框 850–1390）都在畫面裡', () => {
    expect(screenX(w, h, 1, RIGHT_PAGE.x - RIGHT_PAGE.width / 2)).toBeGreaterThanOrEqual(0)
    expect(screenX(w, h, 1, RIGHT_PAGE.x + RIGHT_PAGE.width / 2)).toBeLessThanOrEqual(w)
    expect(screenX(w, h, 1, 850)).toBeGreaterThanOrEqual(0)
    expect(screenX(w, h, 1, 1390)).toBeLessThanOrEqual(w)
  })

  it('書沒有被裁：標籤在書的上方、頁面從紙的上緣開始；書籤在畫面外', () => {
    const layout = bookLayout(w, h, LABELS)
    expect(layout.tagsY).toBe(TOP_Y)
    expect(layout.pageTop).toBe(LEFT_PAGE.top)
    expect(layout.tabs).toBe(false)
    // 標籤（高 44px）整個在畫面裡，而且在右上角的選單（下緣 56px）下面
    expect(screenY(w, h, TOP_Y) - 44).toBeGreaterThanOrEqual(56)
    // 托盤在物件列（離底部約 124px）上面
    expect(screenY(w, h, 940)).toBeLessThan(h - 124)
  })
})

/** 固定在畫面上的東西（手機，px）：選單下緣 56；物件列上緣離底 124；吉祥物 56 見方，離底 130、離右緣 3vw */
const MENU_BOTTOM = 56
const DOCK_TOP = 124
const mascot = (w: number, h: number) => ({ left: w * 0.97 - 56, top: h - 130 - 56 })

describe.each([
  [390, 844], [430, 932], [412, 785], [390, 664], [360, 560], [375, 548], [768, 1024],
])('手機各種高度（%i×%i，含瀏覽器有網址列時）', (w, h) => {
  const layout = bookLayout(w, h, LABELS)
  const unit = visibleBand(w, h, layout.stops[0]).unitPx

  it('是手機版面，兩頁與托盤左右都完整在畫面裡', () => {
    expect(isPhone(w, h)).toBe(true)
    expect(layout.phone).toBe(true)
    expect(screenX(w, h, 0, LEFT_PAGE.x - LEFT_PAGE.width / 2)).toBeGreaterThanOrEqual(0)
    expect(screenX(w, h, 0, LEFT_PAGE.x + LEFT_PAGE.width / 2)).toBeLessThanOrEqual(w)
    expect(screenX(w, h, 1, RIGHT_PAGE.x - RIGHT_PAGE.width / 2)).toBeGreaterThanOrEqual(0)
    expect(screenX(w, h, 1, RIGHT_PAGE.x + RIGHT_PAGE.width / 2)).toBeLessThanOrEqual(w)
    expect(screenX(w, h, 1, TRAY.x + TRAY.width / 2)).toBeLessThanOrEqual(w)
  })

  it('標籤（闔上書、步驟）整個在右上角選單下面，頁面內容從標籤下面開始', () => {
    expect(screenY(w, h, layout.tagsY) - 44).toBeGreaterThanOrEqual(MENU_BOTTOM + 4 - 0.5)
    expect(layout.pageTop).toBeGreaterThan(layout.tagsY)
  })

  it('托盤內側（確定／採用的按鈕在這裡）整個在物件列上面', () => {
    expect(screenY(w, h, TRAY.y)).toBeGreaterThan(0)
    expect(screenY(w, h, TRAY.y + TRAY.height)).toBeLessThanOrEqual(h - DOCK_TOP)
  })

  it('托盤上的按鈕不會被站在右下角的吉祥物蓋住', () => {
    const trayRight = screenX(w, h, 1, TRAY.x + TRAY.width / 2)
    if (screenY(w, h, TRAY.y + TRAY.height) > mascot(w, h).top) {
      expect(trayRight - layout.trayClear).toBeLessThanOrEqual(mascot(w, h).left)
    } else {
      expect(layout.trayClear).toBe(0)
    }
  })

  it('讓完之後頁面和托盤還夠大：放得下 44px 的按鈕與幾列內容', () => {
    expect((RIGHT_PAGE.bottom - layout.pageTop) * unit).toBeGreaterThanOrEqual(240)
    expect(LEFT_PAGE.width * unit).toBeGreaterThanOrEqual(270)
    expect(TRAY.height * unit).toBeGreaterThanOrEqual(44 + 8)
    expect(TRAY.width * unit - layout.trayClear).toBeGreaterThanOrEqual(200)
  })
})

describe('手機（390×664：Safari 有網址列）', () => {
  const [w, h] = [390, 664]

  it('放不下：世界貼齊上緣並再縮小一點，標籤改壓在書上、頁面從標籤下面開始', () => {
    const layout = bookLayout(w, h, LABELS)
    expect(layout.stops[0].letterboxTop).toBe(0)
    expect(layout.stops[0].span).toBeGreaterThan(660)
    expect(layout.stops[1].span).toBe(layout.stops[0].span)
    expect(layout.tagsY).toBeGreaterThan(TOP_Y)
    expect(layout.pageTop).toBeGreaterThan(LEFT_PAGE.top)
    // 托盤剛好在物件列上面（留 4px）
    expect(screenY(w, h, TRAY.y + TRAY.height)).toBeCloseTo(h - DOCK_TOP - 4, 0)
    // 吉祥物站在托盤右端：按鈕往左讓
    expect(layout.trayClear).toBeGreaterThan(0)
  })
})

describe('手機有瀏海與底部橫條（加到主畫面）', () => {
  it('選單與物件列往裡讓多少，標籤與托盤就跟著讓多少', () => {
    const [w, h] = [390, 844]
    const safe = { top: 47, bottom: 34 }
    const layout = bookLayout(w, h, LABELS, safe)
    const band = visibleBand(w, h, layout.stops[0])
    const y = (art: number) => (art - band.top) * band.unitPx
    // 選單：上緣 47、高 44
    expect(y(layout.tagsY) - 44).toBeGreaterThanOrEqual(47 + 44 + 4 - 0.5)
    expect(y(TRAY.y + TRAY.height)).toBeLessThanOrEqual(h - 34 - DOCK_TOP)
  })
})

describe('桌機', () => {
  it('16:10（1440×900）整個世界都看得到：什麼都不用移', () => {
    const layout = bookLayout(1440, 900, LABELS)
    expect(layout.stops[0].fy).toBe(0.5)
    expect(layout.tagsY).toBe(TOP_Y)
    expect(layout.pageTop).toBe(LEFT_PAGE.top)
    expect(layout.tabs).toBe(true)
  })

  it('16:9 全螢幕（1920×1080）：標籤往下壓在封面上，頁面幾乎不用讓；托盤與書籤都看得到', () => {
    const [w, h] = [1920, 1080]
    const layout = bookLayout(w, h, LABELS)
    expect(layout.tagsY).toBeGreaterThan(TOP_Y)
    expect(layout.pageTop).toBeLessThan(LEFT_PAGE.top + 5)
    expect(screenY(w, h, layout.tagsY) - 44).toBeGreaterThanOrEqual(8 - 0.5)
    expect(screenY(w, h, TRAY.y + TRAY.height)).toBeLessThanOrEqual(h)
    expect(layout.tabs).toBe(true)
  })

  it.each([[1920, 950], [1366, 650], [1280, 600]])('扁的瀏覽器視窗（%i×%i）：托盤看得到，頁面從標籤下面開始', (w, h) => {
    const layout = bookLayout(w, h, LABELS)
    // 托盤內側（含按鈕）整個在畫面裡
    expect(screenY(w, h, TRAY.y)).toBeGreaterThan(0)
    expect(screenY(w, h, TRAY.y + TRAY.height)).toBeLessThanOrEqual(h)
    // 標籤整個在畫面裡；頁面內容在標籤與右上角選單（下緣 56px）下面
    expect(screenY(w, h, layout.tagsY) - 44).toBeGreaterThanOrEqual(8 - 0.5)
    expect(layout.pageTop).toBeGreaterThan(LEFT_PAGE.top)
    expect(screenY(w, h, layout.pageTop)).toBeGreaterThanOrEqual(56)
    // 兩頁都還有夠用的高度
    expect((RIGHT_PAGE.bottom - layout.pageTop) * visibleBand(w, h, layout.stops[0]).unitPx).toBeGreaterThan(280)
    // 第一個書籤被切掉了：改用頁首那一排
    expect(layout.tabs).toBe(false)
  })

  it('書籤看得到時，第一個書籤完整落在右上角選單下面', () => {
    for (const [w, h] of [[1440, 900], [1920, 1080], [1600, 900], [1366, 768]] as const) {
      const layout = bookLayout(w, h, LABELS)
      if (layout.tabs) expect(screenY(w, h, TABS.y)).toBeGreaterThanOrEqual(60 - 0.5)
    }
  })
})

describe('平板橫放（1024×768）', () => {
  it('要左右移動鏡頭但上下沒有被裁：位置不用移，書籤改用頁首那一排', () => {
    const layout = bookLayout(1024, 768, LABELS)
    expect(layout.phone).toBe(false)
    expect(layout.trayClear).toBe(0)
    expect(layout.tagsY).toBe(TOP_Y)
    expect(layout.pageTop).toBe(LEFT_PAGE.top)
    expect(layout.tabs).toBe(false)
  })
})

describe('量不到畫面大小', () => {
  it('用預設的位置，不會算出 NaN', () => {
    const layout = bookLayout(0, 0, LABELS)
    expect(layout.tagsY).toBe(TOP_Y)
    expect(layout.pageTop).toBe(LEFT_PAGE.top)
    expect(layout.stops.map((s) => s.fy)).toEqual([0.5, 0.5])
  })
})
