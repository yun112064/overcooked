/**
 * 鏡頭取景（DESIGN.md 4.4、7.3）。
 * 每個房間的插圖是 1600×1000 的「世界」。世界一律放大到蓋滿畫面；
 * 畫面比世界窄（手機直式）時只看得到一段，鏡頭停在各個停靠點上。
 */

export const WORLD_W = 1600
export const WORLD_H = 1000

/** 看得到的世界寬度低於這個比例，就當成需要左右換停靠點。 */
const PAN_THRESHOLD = 0.98
/** 低於這個比例視為手機直式，才套用停靠點自己的 zoom。 */
const PHONE_THRESHOLD = 0.6
/** 世界縮到比畫面矮時，上方留白佔剩餘空間的比例。 */
const LETTERBOX_TOP = 0.3

export type Stop = {
  id: string
  label: string
  /** 停靠點中心在世界寬度上的位置，0–1 */
  fx: number
  /** 停靠點中心在世界高度上的位置，0–1；預設 0.5 */
  fy?: number
  /** 手機直式時再放大的倍率；預設 1 */
  zoom?: number
  /**
   * 手機直式時一定要完整看到的寬度（畫稿單位）。
   * 長螢幕手機蓋滿畫面時只看得到約 460 單位寬；需要更寬時世界會縮小，
   * 上下露出的地方由畫面底色填滿（下方本來就是物件列和分頁）。
   */
  span?: number
  /**
   * 世界為了 span 縮到比畫面矮時，上方留白佔剩餘空間的比例（0＝貼齊上緣、1＝貼齊下緣）；預設 0.3。
   * 場景在畫面下半有自己的抽屜時用小一點的值，把世界往上讓。
   */
  letterboxTop?: number
}

export type Frame = {
  /** 世界在畫面上的寬高（px） */
  width: number
  height: number
  /** 世界左上角相對於畫面左上角的位移（px） */
  left: number
  top: number
}

export function clamp(value: number, low: number, high: number): number {
  return low > high ? (low + high) / 2 : Math.min(high, Math.max(low, value))
}

function coverScale(viewW: number, viewH: number): number {
  return Math.max(viewW / WORLD_W, viewH / WORLD_H)
}

/** 蓋滿畫面時，看得到的世界寬度比例（1＝整個世界都看得到）。 */
export function visibleFraction(viewW: number, viewH: number): number {
  return Math.min(1, viewW / (WORLD_W * coverScale(viewW, viewH)))
}

export function isPanning(viewW: number, viewH: number): boolean {
  return visibleFraction(viewW, viewH) < PAN_THRESHOLD
}

export function isPhone(viewW: number, viewH: number): boolean {
  return visibleFraction(viewW, viewH) < PHONE_THRESHOLD
}

/** 鏡頭停在某個停靠點時，世界該放多大、擺在哪。停靠點太靠邊會被夾回來，不露出世界外面。 */
export function frameFor(viewW: number, viewH: number, stop: Stop): Frame {
  const phone = isPhone(viewW, viewH)
  let scale = coverScale(viewW, viewH) * (phone ? stop.zoom ?? 1 : 1)
  if (phone && stop.span) scale = Math.min(scale, viewW / stop.span)
  const width = WORLD_W * scale
  const height = WORLD_H * scale
  const halfX = Math.min(1, viewW / width) / 2
  const fx = clamp(stop.fx, halfX, 1 - halfX)
  const left = viewW / 2 - fx * width
  // 世界比畫面矮（為了 span 縮小過）：靠上放，把下方留給物件列
  if (height < viewH) return { width, height, left, top: (viewH - height) * clamp(stop.letterboxTop ?? LETTERBOX_TOP, 0, 1) }
  const halfY = viewH / height / 2
  const fy = clamp(stop.fy ?? 0.5, halfY, 1 - halfY)
  return { width, height, left, top: viewH / 2 - fy * height }
}

/**
 * 目前畫面上看得到世界的哪一塊（畫稿單位，已換算掉圖層比世界大的那一圈，可以直接和 <Placed> 的座標比）。
 * 寬螢幕比 16:10 扁時世界的上下會被裁掉，場景可以用它把東西留在看得到的範圍裡。
 */
export function visibleArt(viewW: number, viewH: number, frame: Frame): { left: number; right: number; top: number; bottom: number } {
  // place() 的反函式：畫面上的比例 → 畫稿座標
  const artX = (px: number) => ((px - frame.left) / frame.width - 0.5) / LAYER_BLEED * WORLD_W + WORLD_W / 2
  const artY = (py: number) => ((py - frame.top) / frame.height - 0.5) / LAYER_BLEED * WORLD_H + WORLD_H / 2
  return {
    left: Math.max(0, artX(0)), right: Math.min(WORLD_W, artX(viewW)),
    top: Math.max(0, artY(0)), bottom: Math.min(WORLD_H, artY(viewH)),
  }
}

/** 邊緣差這幾個像素以內都算看得全（四捨五入與視差的小位移） */
const SHOWN_SLACK_PX = 4

/**
 * 鍵盤焦點落在世界裡的某一段時，鏡頭該停在哪個停靠點。
 * left、right 是那個東西左右緣在世界寬度上的比例（0–1，用畫面上量到的位置除以世界的寬度）。
 * 目前的停靠點就看得全 → 不動；否則照順序找第一個看得全的；都看不全（東西比畫面寬）就挑它最靠近畫面中央的那一個。
 */
export function stopShowing(
  stops: readonly Stop[], viewW: number, viewH: number, currentId: string, left: number, right: number,
): string {
  const view = (stop: Stop) => {
    const frame = frameFor(viewW, viewH, stop)
    const from = frame.left + left * frame.width
    const to = frame.left + right * frame.width
    return { whole: from >= -SHOWN_SLACK_PX && to <= viewW + SHOWN_SLACK_PX, offCentre: Math.abs((from + to) / 2 - viewW / 2) }
  }
  const current = stops.find((s) => s.id === currentId)
  if (current && view(current).whole) return currentId
  const whole = stops.find((s) => view(s).whole)
  if (whole) return whole.id
  let best = current ?? stops[0]
  for (const stop of stops) {
    if (view(stop).offCentre < view(best).offCentre) best = stop
  }
  return best?.id ?? currentId
}

/** 整個世界都看得到時不需要停靠點；否則照房間定義的順序。 */
export function activeStops(stops: readonly Stop[], viewW: number, viewH: number): readonly Stop[] {
  return isPanning(viewW, viewH) ? stops : stops.slice(0, 1)
}

/**
 * 圖層比世界大一圈（四邊各多 3%），視差位移時才不會露出邊緣。
 * Room.module.css 的 .layer 用同一個數字，兩邊要一起改。
 */
export const LAYER_BLEED = 1.06

function percent(value: number): string {
  return `${Math.round(value * 1e4) / 1e4}%`
}

/** 把畫稿座標（1600×1000）轉成疊層用的百分比定位，對齊放大過的圖層。 */
export function place(x: number, y: number, width?: number): { left: string; top: string; width?: string } {
  return {
    left: percent(50 + (x / WORLD_W - 0.5) * LAYER_BLEED * 100),
    top: percent(50 + (y / WORLD_H - 0.5) * LAYER_BLEED * 100),
    ...(width === undefined ? {} : { width: percent((width / WORLD_W) * LAYER_BLEED * 100) }),
  }
}
