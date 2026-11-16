import { describe, expect, it } from 'vitest'
import {
  activeStops, frameFor, isPanning, isPhone, place, stopShowing, visibleArt, visibleFraction, type Stop,
} from './framing'

const KITCHEN: Stop[] = [
  { id: 'rail', label: '出菜口與食譜書', fx: 0.2 },
  { id: 'stove', label: '爐台', fx: 0.52 },
  { id: 'fridge', label: '冰箱', fx: 0.824 },
]

/** 這個取景下，世界的哪一段水平範圍（畫稿座標）落在畫面內。 */
function visibleRange(viewW: number, viewH: number, stop: Stop): [number, number] {
  const frame = frameFor(viewW, viewH, stop)
  const unit = frame.width / 1600
  return [-frame.left / unit, (viewW - frame.left) / unit]
}

describe('visibleFraction', () => {
  it('整個世界都看得到的 16:10 畫面是 1', () => {
    expect(visibleFraction(1600, 1000)).toBe(1)
  })

  it('比 16:10 更寬的畫面仍然看得到整個寬度', () => {
    expect(visibleFraction(1920, 1080)).toBe(1)
    expect(isPanning(1920, 1080)).toBe(false)
  })

  it('手機直式只看得到三分之一左右', () => {
    expect(visibleFraction(390, 844)).toBeCloseTo(390 / (1600 * 0.844), 5)
    expect(isPanning(390, 844)).toBe(true)
    expect(isPhone(390, 844)).toBe(true)
  })

  it('平板直式要換停靠點，但不算手機', () => {
    expect(isPanning(820, 1180)).toBe(true)
    expect(isPhone(1024, 768)).toBe(false)
  })
})

describe('frameFor', () => {
  it('沒有指定 span 時世界一律蓋滿畫面，不露出邊界', () => {
    for (const [w, h] of [[390, 844], [820, 1180], [1440, 900], [1920, 1080]] as const) {
      for (const stop of KITCHEN) {
        const frame = frameFor(w, h, stop)
        expect(frame.left).toBeLessThanOrEqual(0.001)
        expect(frame.top).toBeLessThanOrEqual(0.001)
        expect(frame.left + frame.width).toBeGreaterThanOrEqual(w - 0.001)
        expect(frame.top + frame.height).toBeGreaterThanOrEqual(h - 0.001)
      }
    }
  })

  it('長螢幕手機蓋滿畫面時只看得到約 462 單位寬', () => {
    const [left, right] = visibleRange(390, 844, KITCHEN[1])
    expect(right - left).toBeCloseTo(390 / 0.844, 3)
    expect(left).toBeLessThan(660)           // 瓦斯爐（660–1000）完整入鏡
    expect(right).toBeGreaterThan(1000)
    const [fridgeL, fridgeR] = visibleRange(390, 844, KITCHEN[2])
    expect(fridgeL).toBeLessThan(1230)       // 冰箱（1230–1530）完整入鏡
    expect(fridgeR).toBeGreaterThanOrEqual(1530)
  })

  it('停靠點太靠邊會被夾回來，不露出世界外面', () => {
    const [left] = visibleRange(390, 844, { id: 'edge', label: '邊', fx: 0.02 })
    expect(left).toBeCloseTo(0, 5)
    const [, right] = visibleRange(390, 844, { id: 'edge', label: '邊', fx: 0.99 })
    expect(right).toBeCloseTo(1600, 5)
  })

  it('span 讓手機看得到指定的寬度，世界縮小並靠上放', () => {
    const plates: Stop = { id: 'plates', label: '門牌', fx: 0.375, span: 680 }
    const [left, right] = visibleRange(390, 844, plates)
    expect(right - left).toBeCloseTo(680, 3)
    expect(left).toBeLessThan(294)           // 四張門牌（294–906）完整入鏡
    expect(right).toBeGreaterThan(906)
    const frame = frameFor(390, 844, plates)
    expect(frame.height).toBeLessThan(844)
    expect(frame.top).toBeGreaterThan(0)
    expect(frame.top + frame.height).toBeLessThan(844)
  })

  it('span 比蓋滿畫面時看得到的還窄就不起作用；桌機也不受影響', () => {
    const narrow: Stop = { id: 'a', label: 'a', fx: 0.5, span: 300 }
    expect(frameFor(390, 844, narrow).height).toBeCloseTo(844, 5)
    const wide: Stop = { id: 'b', label: 'b', fx: 0.5, span: 680 }
    expect(frameFor(1440, 900, wide).width).toBeCloseTo(1440, 5)
  })

  it('zoom 只在手機直式生效', () => {
    const door: Stop = { id: 'door', label: '大門', fx: 0.5, fy: 0.56, zoom: 1.5 }
    expect(frameFor(390, 844, door).height).toBeCloseTo(844 * 1.5, 5)
    expect(frameFor(1440, 900, door).width).toBeCloseTo(1440, 5)
  })

  it('桌機上不管指定哪個停靠點，都是置中的完整構圖', () => {
    const a = frameFor(1600, 1000, KITCHEN[0])
    const b = frameFor(1600, 1000, KITCHEN[2])
    expect(a).toEqual(b)
    expect(a.left).toBeCloseTo(0, 5)
  })
})

describe('activeStops', () => {
  it('手機用房間定義的全部停靠點', () => {
    expect(activeStops(KITCHEN, 390, 844)).toHaveLength(3)
  })

  it('桌機只剩一個', () => {
    expect(activeStops(KITCHEN, 1440, 900)).toHaveLength(1)
  })
})

describe('place', () => {
  it('畫面正中的座標不受圖層放大影響', () => {
    expect(place(800, 500)).toEqual({ left: '50%', top: '50%' })
  })

  it('其餘座標跟著圖層一起放大 1.06 倍（圖層四邊各多 3%）', () => {
    expect(place(0, 0)).toEqual({ left: '-3%', top: '-3%' })
    expect(place(1600, 1000)).toEqual({ left: '103%', top: '103%' })
    expect(place(800, 500, 160).width).toBe('10.6%')
  })
})

describe('letterboxTop', () => {
  const stop: Stop = { id: 'a', label: 'a', fx: 0.5, span: 900 }

  it('預設把世界放在剩餘空間的 30% 處', () => {
    const frame = frameFor(390, 844, stop)
    expect(frame.height).toBeLessThan(844)
    expect(frame.top).toBeCloseTo((844 - frame.height) * 0.3)
  })

  it('停靠點可以自己指定：0 貼齊上緣、1 貼齊下緣，超出範圍會夾回來', () => {
    const height = frameFor(390, 844, stop).height
    expect(frameFor(390, 844, { ...stop, letterboxTop: 0 }).top).toBe(0)
    expect(frameFor(390, 844, { ...stop, letterboxTop: 1 }).top).toBeCloseTo(844 - height)
    expect(frameFor(390, 844, { ...stop, letterboxTop: -3 }).top).toBe(0)
  })

  it('世界蓋滿畫面時沒有留白，設了也不影響', () => {
    const plain: Stop = { id: 'b', label: 'b', fx: 0.5 }
    expect(frameFor(390, 844, { ...plain, letterboxTop: 0 })).toEqual(frameFor(390, 844, plain))
  })
})

describe('visibleArt', () => {
  it('16:10 的畫面整個世界都看得到（圖層多出來的那一圈不算）', () => {
    const frame = frameFor(1600, 1000, { id: 'a', label: 'a', fx: 0.5 })
    const seen = visibleArt(1600, 1000, frame)
    // 圖層放大 1.06 倍，畫面四邊各少看到約 2.8% 的畫稿
    expect(seen.left).toBeCloseTo(800 - 800 / 1.06)
    expect(seen.right).toBeCloseTo(800 + 800 / 1.06)
    expect(seen.top).toBeCloseTo(500 - 500 / 1.06)
    expect(seen.bottom).toBeCloseTo(500 + 500 / 1.06)
  })

  it('比 16:10 扁的視窗上下被裁掉；和 place() 互為反函式', () => {
    const frame = frameFor(1920, 800, { id: 'a', label: 'a', fx: 0.5 })
    const seen = visibleArt(1920, 800, frame)
    expect(seen.top).toBeGreaterThan(100)
    expect(seen.bottom).toBeLessThan(900)
    // 看得到的上緣那個畫稿 y，用 place() 放回去應該正好在畫面最上面
    const topPx = frame.top + (parseFloat(place(800, seen.top).top) / 100) * frame.height
    expect(topPx).toBeCloseTo(0, 1)
  })

  it('手機只看得到一段寬度', () => {
    const frame = frameFor(390, 844, { id: 'a', label: 'a', fx: 0.5 })
    const seen = visibleArt(390, 844, frame)
    expect(seen.right - seen.left).toBeGreaterThan(400)
    expect(seen.right - seen.left).toBeLessThan(480)
  })
})

describe('stopShowing', () => {
  // 390×844 蓋滿畫面時看得到世界寬度的 28.9%：出菜口 0.056–0.344、爐台 0.376–0.664、冰箱 0.68–0.968
  const at = (currentId: string, left: number, right: number) => stopShowing(KITCHEN, 390, 844, currentId, left, right)

  it('焦點所在的東西在目前的停靠點就看得全：鏡頭不動', () => {
    expect(at('stove', 0.45, 0.6)).toBe('stove')
  })

  it('在別的停靠點才看得全：鏡頭轉過去（例如從出菜口 Tab 到冰箱上的便利貼）', () => {
    expect(at('rail', 0.75, 0.9)).toBe('fridge')
    expect(at('fridge', 0.1, 0.3)).toBe('rail')
  })

  it('只露出一半也算看不全：換到看得全的那一個', () => {
    // 兩個停靠點看得到的範圍有重疊：0.156–0.444 與 0.356–0.644
    const stops: Stop[] = [{ id: 'a', label: '左', fx: 0.3 }, { id: 'b', label: '右', fx: 0.5 }]
    expect(stopShowing(stops, 390, 844, 'a', 0.4, 0.5)).toBe('b')
    expect(stopShowing(stops, 390, 844, 'a', 0.36, 0.44)).toBe('a')
  })

  it('東西比畫面寬，沒有哪一個停靠點看得全：挑它最靠近畫面中央的', () => {
    expect(at('rail', 0.3, 0.75)).toBe('stove')
  })

  it('只有一個停靠點（桌機）就留在原地', () => {
    expect(stopShowing(KITCHEN.slice(0, 1), 1440, 900, 'rail', 0.9, 0.95)).toBe('rail')
  })
})
