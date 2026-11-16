/**
 * 冰箱門上東西的位置（畫稿座標 1600×1000）與鏡頭停靠點。
 *
 * 畫稿裡的東西：冰箱門 180–1420；鍍鉻把手 242–280 × 254–774；門縫 36–56；
 * 門底的裝飾磁鐵在 y 920 以下、迴紋針夾在 1324–1360 × 598–662，便利貼要避開。
 *
 * 注意圖層比世界大一圈（framing.ts 的 LAYER_BLEED）：畫稿上相距 d 的兩點，在世界裡相距 d × 1.06。
 * 停靠點的 fx／span 是「世界」的座標，所以要先用 worldX 換算，不能直接拿畫稿座標除以 1600。
 */
import { LAYER_BLEED, WORLD_H, WORLD_W, clamp, type Frame, type Stop } from '../../camera/framing'

/** 把手的可點範圍：畫稿 232–292 × 246–786（比插圖的把手大一圈，手機上才有 44px 寬） */
export const HANDLE = { x: 262, y: 246, width: 60, height: 540 }
/** 左欄（月曆＋當日餐單）297–727：寬 430，手機上月曆一格才有 44px 寬 */
export const LEFT = { x: 512, width: 430 }
/** 右欄（趨勢＋生成紀錄）880–1320 */
export const RIGHT = { x: 1100, width: 440 }
/** 兩顆大磁鐵的上緣：緊貼門縫下方 */
export const TAB_Y = 60
/** 兩欄便利貼的上緣 */
export const COLUMN_Y = 134

/** 畫稿座標 x → 世界寬度上的比例（0–1），已含圖層放大的那一圈。 */
export function worldX(x: number): number {
  return 0.5 + (x / WORLD_W - 0.5) * LAYER_BLEED
}

/** 畫稿座標 y → 世界高度上的比例（0–1）。 */
export function worldY(y: number): number {
  return 0.5 + (y / WORLD_H - 0.5) * LAYER_BLEED
}

/**
 * 手機上磁鐵那一排最高只能到這裡（px，從畫面上緣算）：右上角的家庭選單在 12–56，再留 4。
 * 家庭名稱長的時候選單會寬到畫面中間，磁鐵從左右都躲不掉，只能排在它下面。
 */
export const MENU_CLEAR_PX = 60

/**
 * 吉祥物的圖從畫面底部往上佔到多高（px）；HUD 與物件列佔掉的高度另外算（樣式裡的 --mascot-inset）。
 * 數字來自 shell/Mascot.module.css：跑道離底部 10px、寬 clamp(56px, 6.5vw, 96px)，圖的高是寬的 7/6（viewBox 120×140）。
 * 吉祥物可以點（會打開小幫手）。手機上它的跑道就在物件列上面，便利貼的按鈕排進去會被它搶走點擊，所以欄的下緣停在它頭頂。
 */
export function mascotReach(viewW: number): number {
  return 10 + clamp(viewW * 0.065, 56, 96) * (7 / 6)
}

const px = (value: number) => Math.round(value * 10) / 10

/**
 * 磁鐵與兩欄便利貼在畫面上要怎麼讓位（px），跟著現在的取景算。
 * - push：整排往下移多少。手機畫面矮的時候（例如瀏覽器工具列佔掉一截），世界貼齊畫面上緣，
 *   磁鐵會跑到家庭選單底下；這時磁鐵和兩欄一起往下讓。畫面夠高或在桌機上是 0。
 * - room：欄的上緣往下有多少高度可以用。手機算到吉祥物頭頂，樣式再扣掉分頁、物件列與安全區，就是欄最多可以多高；
 *   放不下的部分在欄裡上下捲，不會掉到物件列底下點不到。
 *   桌機算到畫面底部，和原本一樣（吉祥物偶爾會跑過最下面那一列；視窗不高的筆電若也讓開，當日餐單只剩一列高，得不償失）。
 */
export function doorFit(
  view: { w: number; h: number }, frame: Pick<Frame, 'top' | 'height'>, phone: boolean,
): { push: number; room: number } {
  const tabTop = frame.top + worldY(TAB_Y) * frame.height
  const push = phone ? Math.max(0, MENU_CLEAR_PX - tabTop) : 0
  const columnTop = frame.top + worldY(COLUMN_Y) * frame.height + push
  const reach = phone ? mascotReach(view.w) : 0
  return { push: px(push), room: px(Math.max(0, view.h - columnTop - reach)) }
}

/**
 * 手機直式的兩個停靠點（桌機整扇門都看得到，只用第一個）。
 * - fy: 0＝鏡頭貼齊門的上緣。寬螢幕（16:9）世界比畫面高，置中會把最上面的兩顆磁鐵切掉。
 * - 已採用：把手（232）到左欄右緣（727）要一起入鏡，在世界裡是 198–723，共 525 單位；
 *   span 再大月曆一格就不到 44px 寬（上限約 546）。
 * - 採購：右欄靠畫面右邊，左邊留一條約 68px 的空隙給「‹ 已採用」的邊緣按鈕，才不會蓋到圖表。
 *   名稱寫短一點（門上的磁鐵才是完整的「營養與採購」），邊緣按鈕才不會太寬。
 */
export const STOPS: Stop[] = [
  { id: 'adopted', label: '已採用', fx: 0.2877, fy: 0, span: 540 },
  { id: 'nutrition', label: '採購', fx: 0.668, fy: 0, span: 576 },
]
