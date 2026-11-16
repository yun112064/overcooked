/**
 * 導覽殼的版面常數（DESIGN.md 4.3）：五個分頁，以及 HUD 在畫面邊緣佔掉多少。
 * 純資料與純函式，SceneHud 用它畫導覽、Room 用它算物件列與吉祥物要讓多高。
 */

export type HudTab = 'today' | 'plan' | 'pick' | 'records' | 'family'

export const HUD_TABS: readonly { id: HudTab; to: string; label: string }[] = [
  { id: 'today', to: '/today', label: '今日' },
  { id: 'plan', to: '/plan', label: '規劃' },
  { id: 'pick', to: '/pick', label: '食譜庫' },
  { id: 'records', to: '/records', label: '紀錄' },
  { id: 'family', to: '/family', label: '家庭' },
]

/** 手機底部分頁的高度；和 tokens.css 的 --tabbar-h 是同一個數字 */
export const TAB_BAR_PX = 56
/** 手機底部動作列的高度；和 tokens.css 的 --actionbar-h 是同一個數字 */
export const ACTION_BAR_PX = 64
/** 桌機浮動動作列連同上下間距佔掉的高度（SceneHud.module.css 的 .actions：下緣 16＋高 56＋間距 8） */
export const WIDE_ACTION_PX = 80
/** 桌機側欄連同左邊間距佔掉的寬度（SceneHud.module.css 的 .rail：左 12＋寬 64＋間距 4） */
export const RAIL_PX = 80

/**
 * HUD 佔掉的畫面邊緣（px）。
 * 手機：動作列取代分頁，所以兩者只算一個；桌機：分頁變成左側欄，動作列浮在底部中央。
 */
export function hudInsets(phone: boolean, tabs: boolean, actions: boolean): { bottom: number; left: number } {
  if (phone) return { bottom: actions ? ACTION_BAR_PX : tabs ? TAB_BAR_PX : 0, left: 0 }
  return { bottom: actions ? WIDE_ACTION_PX : 0, left: tabs ? RAIL_PX : 0 }
}
