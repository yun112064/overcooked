/**
 * 單板這一景的取景（純函式）。
 * 桌機：紙（問卷）和鍋要同時入鏡；視窗太扁時世界上下會被裁掉，紙上的表單要縮到看得到的範圍內，
 * 不然「下一步」會掉到畫面外。手機：鏡頭只看鍋，問卷是下方的抽屜。
 */
import { LAYER_BLEED, WORLD_H, clamp, frameFor, isPanning, isPhone, type Stop } from '../../camera/framing'

/** 畫稿座標：紙在單板上的可寫範圍（讓開上面的金屬夾與右下的摺角） */
export const PAPER = { x: 400, top: 150, bottom: 892, width: 452 }
/** 畫稿座標：左爐口的中心在 (1090, 390)，鍋的俯視圖是 528 見方 */
export const POT = { x: 1090, y: 126, width: 528 }
/** 鍋的狀態小牌：桌機在鍋下方（爐台玻璃下緣）；手機下半是抽屜，改放在鍋上方 */
export const POT_TAG = { x: 1090, y: 664, phoneY: 78 }
/**
 * 生成中貼在鍋旁（右爐口上）的階段卡。右緣停在 1540：疊層比世界大一圈，畫稿 x 超過約 1555 就出畫面了。
 * 只在整個世界都看得到的版面使用；平板與手機看不到右爐口，階段列改寫在紙上／抽屜裡。
 */
export const STAGE_CARD = { x: 1440, y: 170, width: 200 }

// 手機：只看鍋。鍋比蓋滿畫面時看得到的寬度（約 460）還寬，所以拉遠一點
const PHONE_STOP: Stop = { id: 'pot', label: '鍋', fx: POT.x / 1600, span: 700 }
// 桌機與平板橫放：紙的左緣（150）到鍋的右緣（1354）的正中間
const WIDE_STOP: Stop = { id: 'pot', label: '單板與鍋', fx: 0.468 }

export type Layout = {
  /** 手機直式：問卷是下方抽屜 */
  phone: boolean
  /** 看不到整個世界（手機、平板）：左側欄會壓到紙，所以不顯示導覽 */
  panning: boolean
  stop: Stop
}

/** 手機上方的 HUD（回廚房、家庭選單）佔到這裡（px）；鍋的小牌要排在它下面 */
const PHONE_TOP_HUD_PX = 60

/**
 * 手機的停靠點：世界為了看到整口鍋縮小後比畫面矮，預設會放在剩餘空間的 30% 處，
 * 鍋的下緣就落在半開的抽屜後面。改成盡量往上靠，只留到鍋的小牌不會壓到上方 HUD 為止。
 */
function phoneStop(viewW: number, viewH: number): Stop {
  const height = WORLD_H * (viewW / (PHONE_STOP.span ?? viewW))
  const spare = viewH - height
  if (!(spare > 0)) return PHONE_STOP
  const tagOffset = height * (0.5 + (POT_TAG.phoneY / WORLD_H - 0.5) * LAYER_BLEED)
  return { ...PHONE_STOP, letterboxTop: clamp((PHONE_TOP_HUD_PX - tagOffset) / spare, 0, 0.3) }
}

export function layoutFor(viewW: number, viewH: number): Layout {
  const phone = isPhone(viewW, viewH)
  return { phone, panning: isPanning(viewW, viewH), stop: phone ? phoneStop(viewW, viewH) : WIDE_STOP }
}

/** 抽屜的三種高度：收起（只剩把手）、半開、全開 */
export type SheetSize = 'peek' | 'half' | 'full'

/**
 * 抽屜開著時，吉祥物（飲食小幫手）怎麼擺。
 * 舊前端每一頁都叫得到小幫手，填問卷時也要問得到，所以抽屜打開不能把它整個收掉。
 * - 沒有抽屜（問卷夾在單板上）：單板底部的「上一步／儲存草稿／下一步」就在跑道上，來回跑會擋到按鈕，所以站在右下角。
 * - 抽屜收著：照常沿著底部跑。
 * - 桌機的抽屜（視窗太扁時）只佔左邊：站在右下角就不會擋到問卷。
 * - 手機半開：站到抽屜上緣的右邊（lifted）。
 * - 手機全開：抽屜幾乎蓋滿畫面，沒有地方站、也沒有空間開聊天視窗，先不顯示；縮小或收起問卷就回來。
 */
export function mascotFor(drawer: boolean, phone: boolean, sheet: SheetSize): { assistant: boolean | 'parked'; lifted: boolean } {
  if (!drawer) return { assistant: 'parked', lifted: false }
  if (sheet === 'peek') return { assistant: true, lifted: false }
  if (!phone) return { assistant: 'parked', lifted: false }
  return sheet === 'half' ? { assistant: 'parked', lifted: true } : { assistant: false, lifted: false }
}

/** 表單離畫面上下邊緣至少留這麼多（px） */
const EDGE_PX = 10

/** 畫面上的 y（px）→ 畫稿的 y。疊層比世界大一圈（LAYER_BLEED），和 framing.place 是同一個換算的反函式。 */
function artY(screenY: number, frameTop: number, frameHeight: number): number {
  return WORLD_H * (0.5 + ((screenY - frameTop) / frameHeight - 0.5) / LAYER_BLEED)
}

/**
 * 紙上表單的上緣與高度（畫稿單位）：紙的範圍和畫面看得到的範圍取交集。
 * 高度不到 minUnits 時回傳 null（紙幾乎看不到，改用抽屜）。
 */
export function paperArea(viewW: number, viewH: number, stop: Stop, minUnits = 320): { top: number; height: number } | null {
  const frame = frameFor(viewW, viewH, stop)
  if (!(frame.height > 0)) return null
  const top = Math.max(PAPER.top, artY(EDGE_PX, frame.top, frame.height))
  const bottom = Math.min(PAPER.bottom, artY(viewH - EDGE_PX, frame.top, frame.height))
  return bottom - top >= minUnits ? { top, height: bottom - top } : null
}

/** 右上角的家庭選單佔掉的高度（px）；階段卡要排在它下面 */
const MENU_CLEAR_PX = 68

/** 階段卡的上緣（畫稿 y）：視窗很扁時世界上緣被裁掉，卡片往下移到選單下面，不會被切掉或壓到選單。 */
export function stageCardTop(viewW: number, viewH: number, stop: Stop): number {
  const frame = frameFor(viewW, viewH, stop)
  if (!(frame.height > 0)) return STAGE_CARD.y
  return Math.max(STAGE_CARD.y, artY(MENU_CLEAR_PX, frame.top, frame.height))
}

/** 畫稿高度 → 疊層用的百分比高度（相對於世界的高）。 */
export function artHeightPercent(units: number): string {
  return `${Math.round((units / WORLD_H) * LAYER_BLEED * 1e4) / 100}%`
}
