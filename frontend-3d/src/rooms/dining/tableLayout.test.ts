import { describe, expect, it } from 'vitest'
import { LAYER_BLEED, WORLD_H, WORLD_W, frameFor, isPhone } from '../../camera/framing'
import {
  RECEIPT_WIDTH, SETTING_WIDTH, TABLE, TABLE_SPAN, TRAY_WIDTH, layoutAccessories, layoutDishes, layoutSeats, panelSideForSeat,
  seatScale, vesselKind, type Point,
} from './tableLayout'

const fromCentre = (p: Point) => Math.hypot(p.x - TABLE.cx, p.y - TABLE.cy)
const between = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y)
const angleBetween = (a: number, b: number) => {
  const d = Math.abs(a - b) % 360
  return d > 180 ? 360 - d : d
}

describe('vesselKind', () => {
  it('依菜名挑器皿', () => {
    expect(vesselKind({ name: '清蒸鱸魚' })).toBe('seafood')
    expect(vesselKind({ name: '蔥油雞腿' })).toBe('meat')
    expect(vesselKind({ name: '蒜炒高麗菜' })).toBe('vegetable')
    expect(vesselKind({ name: '味噌豆腐湯' })).toBe('soup')
    expect(vesselKind({ name: '地瓜糙米飯' })).toBe('staple')
  })

  it('湯和主食優先於裡面的料', () => {
    expect(vesselKind({ name: '蘿蔔排骨湯' })).toBe('soup')
    expect(vesselKind({ name: '鮮魚湯' })).toBe('soup')
    expect(vesselKind({ name: '海鮮粥' })).toBe('staple')
    expect(vesselKind({ name: '牛肉麵' })).toBe('staple')
    expect(vesselKind({ name: '豬肉鍋貼' })).toBe('staple')
  })

  it('以飯、麵結尾的是主食，就算名字裡有湯、羹、煲', () => {
    expect(vesselKind({ name: '牛肉湯麵' })).toBe('staple')
    expect(vesselKind({ name: '肉羹麵' })).toBe('staple')
    expect(vesselKind({ name: '臘味煲仔飯' })).toBe('staple')
    expect(vesselKind({ name: '鍋燒烏龍', cookingMethod: '煮湯' })).toBe('staple')
    // 麵在中間、最後是湯的仍然是湯
    expect(vesselKind({ name: '麵疙瘩湯' })).toBe('soup')
    expect(vesselKind({ name: '酸辣湯' })).toBe('soup')
  })

  it('海鮮優先於肉、肉優先於蔬菜', () => {
    expect(vesselKind({ name: '蝦仁炒肉絲' })).toBe('seafood')
    expect(vesselKind({ name: '青椒炒肉絲' })).toBe('meat')
  })

  it('名字像但其實不是的不算', () => {
    expect(vesselKind({ name: '魚香茄子' })).toBe('vegetable')
    expect(vesselKind({ name: '牛奶布丁' })).toBe('other')
    expect(vesselKind({ name: '蠔油芥藍' })).toBe('vegetable')
  })

  it('烹調方式是煮湯也算湯', () => {
    expect(vesselKind({ name: '羅宋', cookingMethod: '煮湯' })).toBe('soup')
  })

  it('菜名看不出來時看食材，都看不出來用小碟', () => {
    expect(vesselKind({ name: '三杯', ingredients: ['雞腿肉', '九層塔'] })).toBe('meat')
    expect(vesselKind({ name: '什錦', ingredients: ['鹽', '白蝦'] })).toBe('seafood')
    expect(vesselKind({ name: '茶碗蒸', ingredients: ['雞蛋', '鹽'] })).toBe('other')
    expect(vesselKind({ name: '甜點' })).toBe('other')
  })
})

describe('layoutDishes', () => {
  it('沒有菜就沒有器皿', () => {
    expect(layoutDishes(0)).toEqual([])
  })

  it('4 道以內擺一圈，大小不變', () => {
    for (let count = 1; count <= 4; count += 1) {
      const spots = layoutDishes(count)
      expect(spots).toHaveLength(count)
      expect(new Set(spots.map((s) => Math.round(fromCentre(s)))).size).toBe(1)
      expect(spots.every((s) => s.size === 140)).toBe(true)
    }
  })

  it('4 道時和審圖頁一樣放在四個斜角', () => {
    const spots = layoutDishes(4)
    expect(spots[0].x).toBeCloseTo(800 - 70.7, 0)
    expect(spots[0].y).toBeCloseTo(500 - 70.7, 0)
    expect(spots[2].x).toBeCloseTo(800 + 70.7, 0)
    expect(spots[2].y).toBeCloseTo(500 + 70.7, 0)
  })

  it('超過 4 道縮小排兩圈，都在轉盤裡而且不疊在一起', () => {
    for (let count = 5; count <= 12; count += 1) {
      const spots = layoutDishes(count)
      expect(spots).toHaveLength(count)
      const size = spots[0].size
      expect(size).toBeLessThan(140)
      expect(size).toBeGreaterThanOrEqual(60)
      expect(new Set(spots.map((s) => Math.round(fromCentre(s)))).size).toBe(2)
      for (const spot of spots) expect(fromCentre(spot) + size / 2).toBeLessThanOrEqual(TABLE.susan)
      for (let a = 0; a < count; a += 1) {
        for (let b = a + 1; b < count; b += 1) expect(between(spots[a], spots[b])).toBeGreaterThanOrEqual(size * 0.9)
      }
    }
  })
})

describe('layoutSeats', () => {
  it('第一位坐在畫面下方，圖不旋轉', () => {
    const [seat] = layoutSeats(1)
    expect(seat.angle).toBe(90)
    expect(seat.rotation).toBe(0)
    expect(seat.setting).toEqual({ x: 800, y: 722 })
    // 飯碗在餐墊左側（審圖頁的位置）
    expect(seat.bowl).toEqual({ x: 775.3, y: 720.5 })
    expect(seat.chair.y).toBeGreaterThan(TABLE.cy + TABLE.radius)
  })

  it('4 個人坐在下、左、上、右', () => {
    const seats = layoutSeats(4)
    expect(seats.map((s) => s.angle)).toEqual([90, 180, 270, 0])
    expect(seats[1].setting).toEqual({ x: 578, y: 500 })
    expect(seats[2].setting).toEqual({ x: 800, y: 278 })
    expect(seats[3].setting).toEqual({ x: 1022, y: 500 })
  })

  it('1 到 8 個人：等角度、餐墊在桌上、彼此不重疊', () => {
    for (let count = 1; count <= 8; count += 1) {
      const seats = layoutSeats(count)
      expect(seats).toHaveLength(count)
      const width = SETTING_WIDTH * seats[0].scale
      for (const seat of seats) {
        expect(seat.scale).toBe(seatScale(count))
        // 餐墊外緣不超出桌子、飯碗在餐墊上、椅子在桌子外
        expect(fromCentre(seat.setting) + 54 * seat.scale).toBeLessThanOrEqual(TABLE.radius)
        expect(between(seat.bowl, seat.setting)).toBeLessThan(width / 2)
        expect(fromCentre(seat.chair)).toBeGreaterThan(TABLE.radius)
      }
      for (let i = 0; i < count; i += 1) {
        const next = seats[(i + 1) % count]
        if (count > 1) {
          expect(angleBetween(seats[i].angle, next.angle)).toBeCloseTo(count === 2 ? 180 : 360 / count, 5)
          expect(between(seats[i].setting, next.setting)).toBeGreaterThanOrEqual(width)
        }
      }
    }
  })

  it('5 個人以內不縮小，再多才縮', () => {
    expect([1, 2, 3, 4, 5].map(seatScale)).toEqual([1, 1, 1, 1, 1])
    expect(seatScale(6)).toBeLessThan(1)
    expect(seatScale(8)).toBeGreaterThan(0.6)
    expect(seatScale(8)).toBeLessThan(seatScale(6))
  })
})

describe('layoutAccessories', () => {
  it('4 個人時收據在右下、小卡盤在左下、立牌在左上', () => {
    const { receipt, tray, tent } = layoutAccessories(4)
    expect([receipt.angle, tray.angle, tent.angle]).toEqual([45, 135, 225])
    expect(receipt.x).toBeGreaterThan(TABLE.cx)
    expect(receipt.y).toBeGreaterThan(TABLE.cy)
    expect(tray.x).toBeLessThan(TABLE.cx)
    expect(tent.y).toBeLessThan(TABLE.cy)
    expect(receipt.scale).toBe(1)
  })

  it('沒有人或一兩個人時放在偏好的位置', () => {
    for (const count of [0, 1, 2]) {
      const { receipt, tray, tent } = layoutAccessories(count)
      expect([receipt.angle, tray.angle, tent.angle]).toEqual([45, 135, 225])
    }
  })

  it('1 到 8 個人：三樣東西各在不同的空檔，不壓到任何一份餐墊', () => {
    for (let count = 1; count <= 8; count += 1) {
      const seats = layoutSeats(count)
      const spots = layoutAccessories(count)
      const list = [
        { spot: spots.receipt, width: RECEIPT_WIDTH },
        { spot: spots.tray, width: TRAY_WIDTH },
        { spot: spots.tent, width: 110 },
      ]
      expect(new Set(list.map((item) => item.spot.angle)).size).toBe(3)
      for (const { spot, width } of list) {
        expect(fromCentre(spot)).toBeLessThan(TABLE.radius)
        const half = (Math.asin((width * spot.scale) / 2 / fromCentre(spot)) * 180) / Math.PI
        for (const seat of seats) {
          const seatHalf = (Math.asin((SETTING_WIDTH * seat.scale) / 2 / fromCentre(seat.setting)) * 180) / Math.PI
          // 容許一點點貼邊（3 度以內）
          expect(angleBetween(spot.angle, seat.angle)).toBeGreaterThanOrEqual(seatHalf + half - 3)
        }
      }
    }
  })

  it('人多時跟著縮小，但不會小到看不見', () => {
    expect(layoutAccessories(5).receipt.scale).toBeLessThanOrEqual(1)
    expect(layoutAccessories(8).receipt.scale).toBeGreaterThanOrEqual(0.45)
    expect(layoutAccessories(8).receipt.scale).toBeLessThan(layoutAccessories(5).receipt.scale)
  })
})

describe('panelSideForSeat', () => {
  it('坐在左半邊的人面板放右邊，其他人放左邊', () => {
    // 4 個人：下、左、上、右
    expect([0, 1, 2, 3].map((i) => panelSideForSeat(4, i))).toEqual(['left', 'right', 'left', 'left'])
    expect(panelSideForSeat(1, 0)).toBe('left')
    // 找不到這個座位（資料對不上）時用預設的左邊
    expect(panelSideForSeat(4, 9)).toBe('left')
  })

  it('面板和被點的座位永遠不在同一側', () => {
    for (let count = 1; count <= 8; count += 1) {
      layoutSeats(count).forEach((seat, i) => {
        const side = panelSideForSeat(count, i)
        if (seat.label.x < TABLE.cx - 100) expect(side).toBe('right')
        if (seat.label.x > TABLE.cx + 100) expect(side).toBe('left')
      })
    }
  })
})

describe('手機一屏看得到整桌（DESIGN.md 4.4）', () => {
  /** 畫稿座標在畫面上的 x、以及畫稿一個單位是幾 px（和 Room 的 <Placed> 同一個算法）。 */
  function onScreen(viewW: number, viewH: number) {
    const frame = frameFor(viewW, viewH, { id: 'table', label: '餐桌', fx: 0.5, span: TABLE_SPAN })
    const unit = (frame.width / WORLD_W) * LAYER_BLEED
    return { frame, unit, x: (artX: number) => frame.left + frame.width / 2 + (artX - WORLD_W / 2) * unit }
  }
  /** 名牌最寬的情況：五個 12px 的字加左右留白，約 80px */
  const TAG_HALF_PX = 40

  for (const [viewW, viewH] of [[390, 844], [360, 800], [412, 915]] as const) {
    it(`${viewW}×${viewH}：每個座位、名牌與桌上的東西都完整在畫面裡`, () => {
      expect(isPhone(viewW, viewH)).toBe(true)
      const { frame, unit, x } = onScreen(viewW, viewH)
      // 世界比畫面矮：整張圓桌上下都看得到，下方留給物件列與動作列
      expect(frame.height).toBeLessThan(viewH * 0.6)
      for (let count = 1; count <= 8; count += 1) {
        for (const seat of layoutSeats(count)) {
          expect(x(seat.label.x) - TAG_HALF_PX).toBeGreaterThanOrEqual(0)
          expect(x(seat.label.x) + TAG_HALF_PX).toBeLessThanOrEqual(viewW)
          // 可以按的範圍（餐墊那一圈）至少 44px
          expect(SETTING_WIDTH * seat.scale * unit).toBeGreaterThanOrEqual(44)
        }
        const spots = layoutAccessories(count)
        for (const spot of [spots.receipt, spots.tray, spots.tent]) {
          expect(x(spot.x)).toBeGreaterThan(60)
          expect(x(spot.x)).toBeLessThan(viewW - 60)
        }
      }
      // 7 個器皿以內（一圈或兩圈）畫出來就至少 44px；再多的由樣式的 min-width 補到 44px
      for (let count = 1; count <= 7; count += 1) {
        for (const dish of layoutDishes(count)) expect(dish.size * unit).toBeGreaterThanOrEqual(44)
      }
    })
  }
})

describe('桌機：上下座位的名牌不碰到標題列與動作列', () => {
  /** 標題列的下緣（上緣 12＋高 44）與浮動動作列佔掉的高度（下緣 16＋高 56） */
  const HEADER_BOTTOM_PX = 56
  const ACTION_BAR_PX = 72
  /** 橫的兩行名牌大約 52px 高 */
  const TAG_HALF_PX = 26

  for (const [viewW, viewH] of [[1280, 720], [1366, 768], [1440, 900], [1920, 1080]] as const) {
    it(`${viewW}×${viewH}`, () => {
      expect(isPhone(viewW, viewH)).toBe(false)
      const frame = frameFor(viewW, viewH, { id: 'table', label: '餐桌', fx: 0.5, span: TABLE_SPAN })
      const unit = (frame.height / WORLD_H) * LAYER_BLEED
      const y = (artY: number) => frame.top + frame.height / 2 + (artY - WORLD_H / 2) * unit
      for (let count = 1; count <= 8; count += 1) {
        for (const seat of layoutSeats(count)) {
          expect(y(seat.labelWide.y) - TAG_HALF_PX).toBeGreaterThanOrEqual(HEADER_BOTTOM_PX)
          expect(y(seat.labelWide.y) + TAG_HALF_PX).toBeLessThanOrEqual(viewH - ACTION_BAR_PX)
        }
      }
    })
  }
})
