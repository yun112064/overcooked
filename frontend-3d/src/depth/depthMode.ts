/** 景深效果三段（DESIGN.md 3.6）。設定只存在這台裝置的 localStorage。 */
export type DepthMode = 'immersive' | 'static' | 'off'

export const DEPTH_MODES: readonly DepthMode[] = ['immersive', 'static', 'off']

export const DEPTH_MODE_LABELS: Record<DepthMode, string> = {
  immersive: '沉浸',
  static: '靜態景深',
  off: '關閉',
}

const MODE_KEY = 'depth-mode'
const PROMPTED_KEY = 'depth-motion-prompted'

type Store = Pick<Storage, 'getItem' | 'setItem'>

/** 儲存空間用不了時的替身：每次存取都失敗，下面的函式就各自走到預設值（讀不到當沒設定、問過沒有也不再問）。 */
const BLOCKED: Store = {
  getItem: () => { throw new Error('storage blocked') },
  setItem: () => { throw new Error('storage blocked') },
}

/**
 * 這台裝置的 localStorage。
 * 瀏覽器封鎖儲存空間時，光是讀 window.localStorage 這個屬性就會丟例外（有些內嵌瀏覽器則給 null），
 * 所以要在這裡接住；直接把 window.localStorage 當參數傳，例外會在進到下面的 try 之前就丟出來，整頁變白。
 */
export function deviceStore(): Store {
  try {
    return window.localStorage ?? BLOCKED
  } catch {
    return BLOCKED
  }
}

export function parseDepthMode(value: unknown): DepthMode {
  return DEPTH_MODES.includes(value as DepthMode) ? (value as DepthMode) : 'immersive'
}

/** 讀不到（隱私模式、被封鎖）就當成預設值，不讓頁面壞掉。 */
export function readDepthMode(store: Store): DepthMode {
  try {
    return parseDepthMode(store.getItem(MODE_KEY))
  } catch {
    return 'immersive'
  }
}

export function writeDepthMode(store: Store, mode: DepthMode): void {
  try {
    store.setItem(MODE_KEY, mode)
  } catch {
    // 存不了就只在這次瀏覽生效
  }
}

/**
 * 系統開了「減少動態」而且還沒問過 → 只提示一次，不自動改設定。
 * 已經不是沉浸模式的人不用再問。
 */
export function shouldPromptReducedMotion(store: Store, prefersReduced: boolean, mode: DepthMode): boolean {
  if (!prefersReduced || mode !== 'immersive') return false
  try {
    return store.getItem(PROMPTED_KEY) !== '1'
  } catch {
    return false
  }
}

export function markMotionPrompted(store: Store): void {
  try {
    store.setItem(PROMPTED_KEY, '1')
  } catch {
    // 同上
  }
}
