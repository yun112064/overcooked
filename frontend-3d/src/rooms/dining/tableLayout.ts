/**
 * 餐桌上的東西要擺在哪（畫稿座標 1600×1000；DESIGN.md 5.6）。全部是純函式，位置只跟「有幾道菜、幾個人」有關。
 *
 * - vesselKind()：一道菜用哪一種器皿的圖。結果資料沒有「類別」欄位，只能從菜名（再不行看食材）猜，
 *   所以這只決定代表圖，畫面上不把猜到的類別寫成文字。
 * - layoutDishes(n)：轉盤上 n 個器皿的中心與大小；4 道以內一圈，超過縮小排兩圈。
 * - layoutSeats(n)：n 個座位（椅子、餐墊、飯碗、名牌）沿桌緣等角度排開，第一位坐在畫面下方。
 * - layoutAccessories(n)：收據、小卡盤、家庭合計立牌放在座位之間的空檔。
 */

/** 圓桌與轉盤（src/assets/rooms/dining/z1.svg）。 */
export const TABLE = { cx: 800, cy: 500, radius: 300, susan: 176 } as const

/**
 * 手機直式要看到的寬度（畫稿單位；停靠點的 span）。
 * 左右兩側座位的名牌中心離桌心 322，而名牌的字有下限（12px）不會跟著鏡頭縮小，圖層又比畫稿大一圈（1.06 倍）：
 * 390px 寬的手機要拉遠到 900 單位，名牌才不會被畫面邊緣切掉（tableLayout.test.ts 有算）。
 */
export const TABLE_SPAN = 900

export type Point = { x: number; y: number }

export type VesselKind = 'staple' | 'seafood' | 'meat' | 'vegetable' | 'soup' | 'other'

/** 各種器皿的圖相對於基準直徑的寬度（海鮮是橢圓盤、湯鍋有雙耳、其他是小碟）。 */
export const VESSEL_WIDTH: Record<VesselKind, number> = {
  staple: 1, meat: 1, vegetable: 1, seafood: 1.3, soup: 1.2, other: 0.7,
}

// 名字裡有這些字但不是那一類：魚香茄子沒有魚、牛奶不是肉、雞蛋算其他
const NOT_WHAT_IT_SOUNDS = /魚香|魚露|蠔油|蝦醬|牛奶|牛油果|肉桂|雞蛋/g
const SOUP = /湯|羹|煲|味噌汁|鍋$/
const STAPLE = /飯|粥|麵|米粉|冬粉|河粉|粄條|水餃|鍋貼|饅頭|包子|吐司|三明治|漢堡|披薩|壽司|年糕|蛋餅|煎餅|蔥油餅/
// 名字最後是這些字的，整道就是主食——就算前面有「湯」「羹」「煲」（牛肉湯麵、肉羹麵、煲仔飯）
const STAPLE_ENDING = /(飯|粥|麵|麵線|米粉|冬粉|河粉|粄條|烏龍|水餃|餛飩)$/
const SEAFOOD = /魚|蝦|蟹|蛤|蜆|貝|蚵|牡蠣|魷|花枝|透抽|小卷|章魚|海鮮|鮭|鱸|鯛|鯖|鱈|鮪|鰻/
const MEAT = /雞|鴨|鵝|豬|牛|羊|肉|排骨|里肌|培根|火腿|香腸|臘腸|貢丸|獅子頭/
const VEGETABLE = /菜|菇|豆|瓜|茄|筍|蘿蔔|花椰|菠|芹|椒|玉米|海帶|木耳|蓮藕|芋|山藥|沙拉|時蔬|芥藍/

function mainKind(text: string): VesselKind | null {
  if (SEAFOOD.test(text)) return 'seafood'
  if (MEAT.test(text)) return 'meat'
  if (VEGETABLE.test(text)) return 'vegetable'
  return null
}

/**
 * 這道菜用哪一種器皿。先看菜名：湯 → 主食 → 海鮮 → 肉 → 蔬菜；菜名看不出來再看前幾樣食材；都看不出來用小碟。
 * 順序有意義：「排骨湯」是湯、「海鮮粥」是主食、「蔥油雞腿」是肉；以飯、麵結尾的（「牛肉湯麵」）是主食不是湯。
 */
export function vesselKind(dish: { name: string; cookingMethod?: string | null; ingredients?: readonly string[] }): VesselKind {
  const name = dish.name.replace(NOT_WHAT_IT_SOUNDS, '')
  if (STAPLE_ENDING.test(name)) return 'staple'
  if (SOUP.test(name) || (dish.cookingMethod != null && /湯/.test(dish.cookingMethod))) return 'soup'
  if (STAPLE.test(name)) return 'staple'
  const fromName = mainKind(name)
  if (fromName) return fromName
  const ingredients = (dish.ingredients ?? []).slice(0, 4).join('、').replace(NOT_WHAT_IT_SOUNDS, '')
  return mainKind(ingredients) ?? 'other'
}

const rad = (deg: number) => (deg * Math.PI) / 180
const round = (n: number) => Math.round(n * 10) / 10

/** 從桌子中心往某個方向（度；0＝右、90＝畫面下方）走 r。 */
function polar(r: number, deg: number): Point {
  return { x: round(TABLE.cx + r * Math.cos(rad(deg))), y: round(TABLE.cy + r * Math.sin(rad(deg))) }
}

// ---- 轉盤上的菜 ----

export type DishSpot = Point & {
  /** 基準直徑（圓盤的寬）；實際圖寬再乘上 VESSEL_WIDTH */
  size: number
}

/** 一圈放得下時的大小（和審圖頁一樣） */
const DISH_FULL = 140
/** 排兩圈時最大的大小 */
const DISH_SMALL_MAX = 104
/** 4 道以內：圈的半徑與第一個的角度。1 道放在花瓶前面，2 道左右，3、4 道平均分。 */
const ONE_RING: Record<number, { radius: number; start: number }> = {
  1: { radius: 84, start: 90 },
  2: { radius: 96, start: 180 },
  3: { radius: 100, start: -90 },
  4: { radius: 100, start: -135 },
}

function ring(count: number, radius: number, start: number, size: number): DishSpot[] {
  return Array.from({ length: count }, (_, i) => ({ ...polar(radius, start + (i * 360) / count), size }))
}

/** 同一圈上相鄰兩個中心的距離。 */
function chord(radius: number, count: number): number {
  return count <= 1 ? Number.POSITIVE_INFINITY : 2 * radius * Math.sin(Math.PI / count)
}

/** 內圈 inner 個、外圈 outer 個時，放得下的最大直徑與兩圈的半徑。 */
function fitTwoRings(inner: number, outer: number): { size: number; outerRadius: number; innerRadius: number } | null {
  for (let size = DISH_SMALL_MAX; size >= 40; size -= 2) {
    const outerRadius = TABLE.susan - size / 2 - 4
    if (chord(outerRadius, outer) < size * 1.02) continue
    const innerRadius = inner === 1 ? 0 : (size * 0.52) / Math.sin(Math.PI / inner)
    if (outerRadius - innerRadius < size * 0.92) continue
    return { size, outerRadius, innerRadius }
  }
  return null
}

/** 轉盤上 count 個器皿的位置。超過 4 個時先排外圈、最後幾個放內圈。 */
export function layoutDishes(count: number): DishSpot[] {
  if (count <= 0) return []
  if (count <= 4) return ring(count, ONE_RING[count].radius, ONE_RING[count].start, DISH_FULL)
  let best: { inner: number; outer: number; size: number; outerRadius: number; innerRadius: number } | null = null
  for (let inner = 1; inner <= Math.floor(count / 2); inner += 1) {
    const fit = fitTwoRings(inner, count - inner)
    if (fit && (!best || fit.size > best.size)) best = { inner, outer: count - inner, ...fit }
  }
  // 多到算不出來（實際上不會發生：一餐最多十來道）就全部擠在外圈
  if (!best) return ring(count, TABLE.susan - 24, -90, 40)
  return [
    ...ring(best.outer, best.outerRadius, -90, best.size),
    ...ring(best.inner, best.innerRadius, 90, best.size),
  ]
}

// ---- 座位 ----

/** 餐墊的寬度與它在桌上的位置（審圖頁：中心離桌心 222、寬 170） */
export const SETTING_WIDTH = 170
const SETTING_DEPTH = 108
const SETTING_RADIUS = 222
/** 飯碗在餐墊上的位置（相對餐墊中心，餐墊還沒旋轉、寬 170 時） */
const BOWL_OFFSET = { x: -24.7, y: -1.5 }
export const BOWL_WIDTH = 84
/** 名牌放在椅子上（桌緣外面一點） */
const LABEL_RADIUS = 322
/**
 * 桌機的名牌是橫的兩行，上下兩個座位的名牌可以往桌緣靠一點：
 * 16:9 的螢幕上下會被裁掉一些，靠外面的話名牌會和上方的標題列、下方的動作列疊在一起。
 */
const LABEL_RADIUS_WIDE_Y = 296

export type SeatSpot = {
  /** 座位在桌子的哪個方向（度；0＝右、90＝畫面下方） */
  angle: number
  /** 椅子與餐墊的圖要轉幾度（畫面下方的座位是 0） */
  rotation: number
  /** 人多時整組縮小的倍率 */
  scale: number
  setting: Point
  bowl: Point
  chair: Point
  /** 名牌的中心（手機：直的一小塊） */
  label: Point
  /** 名牌的中心（桌機與平板：橫的兩行） */
  labelWide: Point
}

function arcPerSeat(count: number): number {
  return (2 * Math.PI * SETTING_RADIUS) / Math.max(1, count)
}

/** 人多時餐墊要縮小，留出空檔給收據和小卡盤；5 人以內不縮。 */
export function seatScale(count: number): number {
  return Math.min(1, (arcPerSeat(count) * 0.62) / SETTING_WIDTH)
}

/** count 個座位沿桌緣等角度排開；第一位在畫面下方，順時針排。 */
export function layoutSeats(count: number): SeatSpot[] {
  const scale = seatScale(count)
  // 縮小後往桌緣靠，餐墊外緣和桌邊的距離不變
  const settingRadius = TABLE.radius - 24 - (SETTING_DEPTH / 2) * scale
  const chairRadius = TABLE.radius + 20 + 76.5 * scale
  return Array.from({ length: Math.max(0, count) }, (_, i) => {
    const angle = (90 + (i * 360) / count) % 360
    const rotation = angle - 90
    const setting = polar(settingRadius, angle)
    const cos = Math.cos(rad(rotation))
    const sin = Math.sin(rad(rotation))
    const ox = BOWL_OFFSET.x * scale
    const oy = BOWL_OFFSET.y * scale
    return {
      angle, rotation, scale, setting,
      bowl: { x: round(setting.x + ox * cos - oy * sin), y: round(setting.y + ox * sin + oy * cos) },
      chair: polar(chairRadius, angle),
      label: polar(LABEL_RADIUS, angle),
      labelWide: {
        x: round(TABLE.cx + LABEL_RADIUS * Math.cos(rad(angle))),
        y: round(TABLE.cy + LABEL_RADIUS_WIDE_Y * Math.sin(rad(angle))),
      },
    }
  })
}

/**
 * 點某個座位時，面板放哪一邊才不會蓋住他自己的名牌。
 * 預設放左邊（DESIGN.md 5.6：左＝個人營養）；坐在桌子左半邊的人改放右邊。
 */
export function panelSideForSeat(count: number, index: number): 'left' | 'right' {
  const spot = layoutSeats(count)[index]
  return spot && spot.label.x < TABLE.cx - 100 ? 'right' : 'left'
}

// ---- 收據、小卡盤、家庭合計立牌 ----

export type AccessorySpot = Point & {
  /** 所在的方向（度） */
  angle: number
  /** 人多時縮小的倍率 */
  scale: number
}

export const RECEIPT_WIDTH = 132
export const TRAY_WIDTH = 118

// 各自偏好的方向與離桌心的距離：收據在右下、小卡盤在左下、立牌在左上（和審圖頁一樣）
const ACCESSORIES = [
  { prefer: 45, radius: 216 },
  { prefer: 135, radius: 225 },
  { prefer: 225, radius: 240 },
] as const

function angleBetween(a: number, b: number): number {
  const d = Math.abs(a - b) % 360
  return d > 180 ? 360 - d : d
}

/** 收據、小卡盤、立牌放在座位之間的空檔，各佔一個、離自己偏好的方向最近的那一個。 */
export function layoutAccessories(seatCount: number): Record<'receipt' | 'tray' | 'tent', AccessorySpot> {
  const seats = layoutSeats(seatCount)
  // 兩個人以內桌邊很空，直接放在偏好的方向；三個人以上放在相鄰兩個座位的正中間
  const gaps = seats.length >= 3 ? seats.map((seat) => (seat.angle + 180 / seats.length) % 360) : null
  const scale = seats.length <= 4
    ? 1
    : Math.max(0.45, Math.min(1, (arcPerSeat(seats.length) - seatScale(seats.length) * SETTING_WIDTH - 8) / 112))
  const used = new Set<number>()
  const spots = ACCESSORIES.map(({ prefer, radius }) => {
    let angle: number = prefer
    if (gaps) {
      const free = gaps.filter((gap) => !used.has(gap))
      angle = free.reduce((best, gap) => (angleBetween(gap, prefer) < angleBetween(best, prefer) ? gap : best), free[0])
      used.add(angle)
    }
    return { ...polar(radius, angle), angle, scale }
  })
  return { receipt: spots[0], tray: spots[1], tent: spots[2] }
}
