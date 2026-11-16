import { describe, expect, it } from 'vitest'
import { LAYER_BLEED, WORLD_H, WORLD_W, frameFor } from '../../camera/framing'
import { PAPER, POT, POT_TAG, STAGE_CARD, artHeightPercent, layoutFor, mascotFor, paperArea, stageCardTop } from './layout'

/** 畫稿座標 → 畫面上的 px（和 framing.place 同一個換算）。 */
function screen(viewW: number, viewH: number, x: number, y: number) {
  const { stop } = layoutFor(viewW, viewH)
  const frame = frameFor(viewW, viewH, stop)
  return {
    x: frame.left + frame.width * (0.5 + (x / WORLD_W - 0.5) * LAYER_BLEED),
    y: frame.top + frame.height * (0.5 + (y / WORLD_H - 0.5) * LAYER_BLEED),
  }
}

describe('mascotFor（抽屜開著時吉祥物怎麼擺）', () => {
  it('沒有抽屜（問卷在紙上）：站在右下角，不擋單板底部的按鈕', () => {
    expect(mascotFor(false, false, 'half')).toEqual({ assistant: 'parked', lifted: false })
  })

  it('抽屜收著：照常跑', () => {
    expect(mascotFor(true, true, 'peek')).toEqual({ assistant: true, lifted: false })
    expect(mascotFor(true, false, 'peek')).toEqual({ assistant: true, lifted: false })
  })

  it('手機半開（一進來的樣子）：還在，站到抽屜上緣', () => {
    expect(mascotFor(true, true, 'half')).toEqual({ assistant: 'parked', lifted: true })
  })

  it('手機全開：抽屜蓋滿畫面，先不顯示', () => {
    expect(mascotFor(true, true, 'full')).toEqual({ assistant: false, lifted: false })
  })

  it('桌機的抽屜只佔左邊：半開、全開都站在右下角', () => {
    expect(mascotFor(true, false, 'half')).toEqual({ assistant: 'parked', lifted: false })
    expect(mascotFor(true, false, 'full')).toEqual({ assistant: 'parked', lifted: false })
  })
})

describe('layoutFor', () => {
  it('手機直式：鏡頭只看鍋，問卷是抽屜', () => {
    const layout = layoutFor(390, 844)
    expect(layout.phone).toBe(true)
    expect(layout.panning).toBe(true)
  })

  it('桌機：看得到整個世界，保留左側欄', () => {
    expect(layoutFor(1440, 900)).toMatchObject({ phone: false, panning: false })
    expect(layoutFor(1920, 1080)).toMatchObject({ phone: false, panning: false })
  })

  it('平板橫放：不是手機，但看不到整個世界（不顯示側欄）', () => {
    expect(layoutFor(1024, 768)).toMatchObject({ phone: false, panning: true })
  })
})

describe('手機看得到整口鍋（DESIGN 4.4）', () => {
  it.each([[390, 844], [375, 667], [360, 780], [430, 932]])('%i×%i', (w, h) => {
    const topLeft = screen(w, h, POT.x - POT.width / 2, POT.y)
    const right = screen(w, h, POT.x + POT.width / 2, POT.y)
    expect(topLeft.x).toBeGreaterThanOrEqual(0)
    expect(right.x).toBeLessThanOrEqual(w)
    expect(topLeft.y).toBeGreaterThanOrEqual(0)
    // 鍋的上半（到圓心）一定在半開的抽屜（畫面下方 54%）上面
    expect(screen(w, h, POT.x, POT.y + POT.width / 2).y).toBeLessThan(h * 0.46)
  })
})

describe('手機：世界往上靠，鍋盡量露在半開的抽屜上面', () => {
  it.each([[390, 844], [360, 780], [430, 932], [414, 896]])('%i×%i：鍋被抽屜擋住的不到一成，小牌在上方 HUD（60px）下面', (w, h) => {
    const potTop = screen(w, h, POT.x, POT.y).y
    const potBottom = screen(w, h, POT.x, POT.y + POT.width).y
    const hidden = Math.max(0, potBottom - h * 0.46)
    expect(hidden / (potBottom - potTop)).toBeLessThan(0.1)
    expect(screen(w, h, POT_TAG.x, POT_TAG.phoneY).y).toBeGreaterThanOrEqual(59.9)
  })

  it('矮的手機（375×667）放不下：小牌仍在 HUD 下面，世界不會比預設的位置更低', () => {
    const { stop } = layoutFor(375, 667)
    expect(stop.letterboxTop).toBeLessThanOrEqual(0.3)
    expect(screen(375, 667, POT_TAG.x, POT_TAG.phoneY).y).toBeGreaterThanOrEqual(59.9)
  })
})

describe('桌機與平板：紙和鍋同時入鏡', () => {
  it.each([[1440, 900], [1280, 720], [1920, 1080], [1024, 768], [1180, 820]])('%i×%i', (w, h) => {
    const paperLeft = screen(w, h, PAPER.x - PAPER.width / 2, PAPER.top)
    const paperRight = screen(w, h, PAPER.x + PAPER.width / 2, PAPER.top)
    const potRight = screen(w, h, POT.x + POT.width / 2, POT.y)
    expect(paperLeft.x).toBeGreaterThanOrEqual(0)
    expect(potRight.x).toBeLessThanOrEqual(w)
    // 紙上的表單至少有 340px 寬（放得下欄位）
    expect(paperRight.x - paperLeft.x).toBeGreaterThanOrEqual(340)
  })

  it('看得到整個世界時，表單讓得開左側欄（76px）', () => {
    for (const [w, h] of [[1440, 900], [1280, 800], [1920, 1080]]) {
      expect(screen(w, h, PAPER.x - PAPER.width / 2, PAPER.top).x).toBeGreaterThan(76)
    }
  })
})

describe('paperArea（紙上表單的範圍）', () => {
  it('16:10 的視窗：整張紙都用得到', () => {
    const { stop } = layoutFor(1440, 900)
    expect(paperArea(1440, 900, stop)).toEqual({ top: PAPER.top, height: PAPER.bottom - PAPER.top })
  })

  it('視窗比較扁：世界上下被裁掉，表單縮到看得到的範圍內（按鈕不會掉到畫面外）', () => {
    const [w, h] = [1440, 700]
    const { stop } = layoutFor(w, h)
    const area = paperArea(w, h, stop)
    expect(area).not.toBeNull()
    if (!area) return
    expect(area.height).toBeLessThan(PAPER.bottom - PAPER.top)
    expect(screen(w, h, PAPER.x, area.top).y).toBeGreaterThanOrEqual(9.9)
    expect(screen(w, h, PAPER.x, area.top + area.height).y).toBeLessThanOrEqual(h - 9.9)
  })

  it('扁到紙幾乎看不到：回傳 null，改用抽屜', () => {
    const { stop } = layoutFor(1600, 300)
    expect(paperArea(1600, 300, stop)).toBeNull()
  })
})

describe('artHeightPercent', () => {
  it('畫稿高度換成世界高度的百分比（含圖層放大的那一圈）', () => {
    expect(artHeightPercent(500)).toBe('53%')
    expect(artHeightPercent(742)).toBe('78.65%')
  })
})

describe('鍋旁的階段卡（桌機生成中）', () => {
  // 會用到階段卡的版面：整個世界都看得到
  const WIDE = [[1440, 900], [1280, 720], [1280, 800], [1366, 768], [1920, 1080], [1920, 800]]

  it.each(WIDE)('%i×%i：整張卡在畫面內，不壓到鍋，也讓開右上角的選單', (w, h) => {
    const layout = layoutFor(w, h)
    expect(layout.panning).toBe(false)
    const top = stageCardTop(w, h, layout.stop)
    const left = screen(w, h, STAGE_CARD.x - STAGE_CARD.width / 2, top)
    const right = screen(w, h, STAGE_CARD.x + STAGE_CARD.width / 2, top)
    expect(right.x).toBeLessThanOrEqual(w - 8)
    expect(left.y).toBeGreaterThanOrEqual(64)
    // 鍋身（圓）的右緣：圖是 480 見方、鍋的半徑 200
    const potEdge = screen(w, h, POT.x + (POT.width / 2) * (200 / 240), top)
    expect(left.x).toBeGreaterThan(potEdge.x)
    // 放得下最長的階段名稱（7 個字 ＋ 記號）
    expect(right.x - left.x).toBeGreaterThanOrEqual(160)
  })

  it('16:10：卡片在畫稿上的原位', () => {
    const layout = layoutFor(1440, 900)
    expect(stageCardTop(1440, 900, layout.stop)).toBe(STAGE_CARD.y)
  })

  it('視窗很扁：世界上緣被裁掉，卡片往下移', () => {
    const layout = layoutFor(1920, 800)
    expect(stageCardTop(1920, 800, layout.stop)).toBeGreaterThan(STAGE_CARD.y)
  })
})
