/**
 * 食譜書這一景的取景（純函式）。
 *
 * 房間的插圖一律放大到蓋滿畫面，所以：
 * - 手機直式一次只看得到一頁：兩個停靠點各自對準一頁，並用 span 拉遠到整頁（和托盤）都看得到。
 *   拉遠後世界比畫面矮，要擺多高由這裡算（phoneFit）：標籤排在右上角選單下面、托盤排在物件列上面。
 * - 視窗比 16:10 扁（一般桌機的瀏覽器視窗都是）時，世界的上下會被裁掉。托盤在最下面、「確定／採用」在托盤上，
 *   所以鏡頭往下靠到托盤看得到為止；被裁掉的是書的上緣，頁面內容與上方的標籤就改從看得到的地方開始排。
 *
 * 座標都是畫稿單位（1600×1000）。疊層比世界大一圈（LAYER_BLEED），換算要和 framing.place 一致。
 */
import { LAYER_BLEED, WORLD_H, WORLD_W, clamp, frameFor, isPanning, isPhone, type Stop } from '../../camera/framing'
import { TAB_BAR_PX } from '../../shell/hudLayout'

/** 左頁的紙：204–798 × 126–874 */
export const LEFT_PAGE = { x: 501, top: 126, bottom: 874, width: 594 }
/** 右頁的紙：802–1396，下緣被托盤壓住（托盤外框上緣 790） */
export const RIGHT_PAGE = { x: 1099, top: 126, bottom: 790, width: 594 }
/** 托盤內側：868–1372 × 806–924（外框 850–1390 × 790–940） */
export const TRAY = { x: 1120, y: 806, width: 504, height: 118 }
/** 右緣六個書籤：封面外露出 1424–1478；按鈕往書裡多蓋一點（1408 起）才夠寬好按。第一個上緣 150，每個間隔 96 */
export const TABS = { x: 1443, y: 150, step: 96, width: 70, height: 84 }
/** 書上方的檯面（封面上緣是 104）：標籤的下緣平常貼在這條線上，往上長 */
export const TOP_Y = 98

/** 手機一個停靠點要看到的寬度（世界單位）：一頁 594 畫稿單位 ≈ 630 世界單位，左右各留一點 */
const PHONE_SPAN = 660
/** 鏡頭往下靠時，至少要看到這裡（托盤內側下緣 924 再多一點） */
const NEED_BOTTOM = 932
/** 上方標籤（闔上書、步驟）的高度與它離畫面上緣、離頁面內容的距離（px） */
const TAG_PX = 44
const EDGE_PX = 8
const GAP_PX = 6
/** 右上角家庭選單的下緣（px，離上緣 12＋高 44＋間距 4）；書籤要完整落在它下面才顯示，手機的標籤也排在它下面 */
const MENU_PX = 60
/** 家庭選單離畫面上緣的距離（px）；有瀏海時它會往下讓到瀏海下面（SessionMenu.module.css） */
const MENU_TOP_PX = 12
/**
 * 手機底部固定在畫面上的東西（px，都從畫面底往上算）。數字來自 Room、SceneHud、Mascot 的樣式，那邊改了這裡要跟著改。
 * - 物件列：在分頁上面 12，高 56（按鈕 44＋上下各 6）；整條都會接走點擊，托盤要完整在它上面。
 * - 站在右下角的吉祥物（assistant="parked"）：在分頁上面 物件列跑道 64＋10，離右緣 3vw，大小 clamp(56px, 6.5vw, 96px)。
 */
const DOCK_TOP_PX = TAB_BAR_PX + 12 + 56
const MASCOT_BOTTOM_PX = TAB_BAR_PX + 64 + 10
const MASCOT_RIGHT = 0.03
const mascotSize = (viewW: number) => clamp(viewW * 0.065, 56, 96)
/** 托盤、標籤和固定在畫面上的東西之間留的空隙（px） */
const HUD_GAP_PX = 4

/** 瀏海與底部橫條佔掉的高度（px）；固定在畫面上的東西會往裡讓這麼多 */
export type SafeArea = { top: number; bottom: number }
const NO_SAFE_AREA: SafeArea = { top: 0, bottom: 0 }

/** 畫稿的 x → 停靠點的 fx（世界寬度的比例）。 */
export function fxOf(x: number): number {
  return 0.5 + (x / WORLD_W - 0.5) * LAYER_BLEED
}

/**
 * 鏡頭的垂直中心：整個世界看得到時在正中間；上下被裁掉時往下靠，直到托盤看得到。
 * （手機為了 span 縮小後世界比畫面矮，這個值不會被用到。）
 */
export function cameraFy(viewW: number, viewH: number): number {
  const worldHeight = WORLD_H * Math.max(viewW / WORLD_W, viewH / WORLD_H)
  if (!(worldHeight > 0) || viewH >= worldHeight) return 0.5
  const visibleUnits = (viewH / worldHeight) * WORLD_H / LAYER_BLEED
  const centre = Math.max(WORLD_H / 2, NEED_BOTTOM - visibleUnits / 2)
  return 0.5 + (centre / WORLD_H - 0.5) * LAYER_BLEED
}

/** 畫稿的 y → 世界高度的比例（和 fxOf 同一個換算）。 */
function fyOf(y: number): number {
  return 0.5 + (y / WORLD_H - 0.5) * LAYER_BLEED
}

type PhoneFit = {
  /** 停靠點要看到的寬度（世界單位）；畫面太矮時比 PHONE_SPAN 大，世界再縮小一點 */
  span: number
  /** 世界上方留白佔剩餘空間的比例（Stop.letterboxTop） */
  letterboxTop: number
  /** 上方標籤的上緣離畫面上緣多遠（px） */
  tagsTop: number
}

/**
 * 手機：世界要放多大、擺多高。
 * 交給鏡頭預設的話世界固定擺在剩餘空間的 30% 處，只有 844px 高的畫面剛好對得上；瀏覽器有網址列時（390×664）
 * 標籤會跑到家庭選單後面、托盤的按鈕會跑到物件列後面按不到。所以自己算：
 * 1. 標籤貼在選單下面；
 * 2. 托盤因此壓到物件列時，世界往上讓（標籤改壓在書上、頁面變矮），最多貼齊畫面上緣；
 * 3. 還是放不下，才把世界縮小到托盤剛好在物件列上面。
 */
function phoneFit(viewW: number, viewH: number, safe: SafeArea): PhoneFit | null {
  const tagsTop = MENU_PX + Math.max(0, safe.top - MENU_TOP_PX)
  // 托盤內側下緣最低能到哪（px）
  const trayLimit = viewH - safe.bottom - DOCK_TOP_PX - HUD_GAP_PX
  const trayAt = fyOf(TRAY.y + TRAY.height)
  const cover = WORLD_H * Math.max(viewW / WORLD_W, viewH / WORLD_H)
  const height = Math.min(cover, (WORLD_H * viewW) / PHONE_SPAN, trayLimit / trayAt)
  const spare = viewH - height
  if (!(height > 0 && spare > 0)) return null
  const top = clamp(tagsTop + TAG_PX - height * fyOf(TOP_Y), 0, Math.max(0, trayLimit - height * trayAt))
  return { span: (viewW * WORLD_H) / height, letterboxTop: clamp(top / spare, 0, 1), tagsTop }
}

export function bookStops(
  viewW: number, viewH: number, labels: readonly [string, string], phone?: Pick<PhoneFit, 'span' | 'letterboxTop'> | null,
): Stop[] {
  const fy = cameraFy(viewW, viewH)
  const framing = { fy, span: phone?.span ?? PHONE_SPAN, ...(phone ? { letterboxTop: phone.letterboxTop } : {}) }
  return [
    { id: 'list', label: labels[0], fx: fxOf(LEFT_PAGE.x), ...framing },
    { id: 'detail', label: labels[1], fx: fxOf(RIGHT_PAGE.x), ...framing },
  ]
}

/** 鏡頭停在某個停靠點時，畫面上緣與下緣落在畫稿的哪個 y，以及一個畫稿單位是幾 px。 */
export function visibleBand(viewW: number, viewH: number, stop: Stop): { top: number; bottom: number; unitPx: number } {
  const frame = frameFor(viewW, viewH, stop)
  const artY = (screenY: number) => WORLD_H * (0.5 + ((screenY - frame.top) / frame.height - 0.5) / LAYER_BLEED)
  return { top: artY(0), bottom: artY(viewH), unitPx: (frame.height * LAYER_BLEED) / WORLD_H }
}

export type BookLayout = {
  /** 手機直式（一次只看得到一頁，底部有物件列） */
  phone: boolean
  stops: Stop[]
  /** 上方標籤（闔上書、步驟）的下緣 */
  tagsY: number
  /** 兩頁內容的上緣：平常是紙的上緣；書的上緣被裁掉時從標籤下面開始 */
  pageTop: number
  /** 右緣的書籤看不看得到；看不到時左頁頁首多一排類型按鈕 */
  tabs: boolean
  /** 托盤右邊要讓出多少（px）：手機上站在右下角的吉祥物會蓋到托盤右端，按鈕要排在牠左邊。蓋不到時是 0 */
  trayClear: number
}

const round = (value: number) => Math.round(value * 10) / 10

/** 手機：托盤（細節那一站）被站在右下角的吉祥物蓋到多寬（px）；上下沒有重疊就是 0。 */
function mascotOverlap(viewW: number, viewH: number, detail: Stop, safe: SafeArea): number {
  const frame = frameFor(viewW, viewH, detail)
  const band = visibleBand(viewW, viewH, detail)
  const trayBottom = (TRAY.y + TRAY.height - band.top) * band.unitPx
  const mascotTop = viewH - safe.bottom - MASCOT_BOTTOM_PX - mascotSize(viewW)
  if (trayBottom <= mascotTop - HUD_GAP_PX) return 0
  const trayRight = frame.left + frame.width * fxOf(TRAY.x + TRAY.width / 2)
  const mascotLeft = viewW * (1 - MASCOT_RIGHT) - mascotSize(viewW)
  return Math.max(0, trayRight - mascotLeft + HUD_GAP_PX)
}

export function bookLayout(
  viewW: number, viewH: number, labels: readonly [string, string], safe: SafeArea = NO_SAFE_AREA,
): BookLayout {
  const fallback: BookLayout = {
    phone: false, stops: bookStops(viewW, viewH, labels), tagsY: TOP_Y, pageTop: LEFT_PAGE.top, tabs: false, trayClear: 0,
  }
  if (!(viewW > 0 && viewH > 0)) return fallback
  const onPhone = isPhone(viewW, viewH)
  const phone = onPhone ? phoneFit(viewW, viewH, safe) : null
  const stops = phone ? bookStops(viewW, viewH, labels, phone) : fallback.stops
  // 兩個停靠點只差左右，上下看到的範圍相同
  const band = visibleBand(viewW, viewH, stops[0])
  if (!(band.unitPx > 0)) return { ...fallback, phone: onPhone }
  // 標籤的上緣：桌機離畫面上緣一點就好（選單在右邊、不同一欄）；手機的標籤和選單同一欄，要排在選單下面
  const tagsY = Math.max(TOP_Y, band.top + ((phone?.tagsTop ?? EDGE_PX) + TAG_PX) / band.unitPx)
  const pageTop = Math.max(LEFT_PAGE.top, tagsY + GAP_PX / band.unitPx)
  return {
    phone: onPhone,
    stops,
    tagsY: round(tagsY),
    pageTop: round(pageTop),
    // 要左右移動鏡頭的版面（手機、平板）在清單那一站看不到右緣；上緣被裁掉時第一個書籤會被切到
    tabs: !isPanning(viewW, viewH) && band.top + MENU_PX / band.unitPx <= TABS.y,
    trayClear: phone ? Math.ceil(mascotOverlap(viewW, viewH, stops[1], safe)) : 0,
  }
}

/** 畫稿高度 → 疊層用的百分比高度（相對於世界的高）。 */
export function artHeight(units: number): string {
  return `${Math.round((units / WORLD_H) * LAYER_BLEED * 1e4) / 100}%`
}
