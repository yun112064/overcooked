/**
 * 進單板時要畫什麼（純函式）：等資料、讀取失敗，還是打開問卷。
 *
 * 問卷一旦打開就一直開著：背景重讀失敗（手機訊號回來時自動重讀、又斷掉）不能把表單換成錯誤畫面，
 * 不然還沒儲存的回答、辨識結果和目前的步驟都會不見。問卷本來就不看重讀回來的資料（它自己記著後端最新的那一版），
 * 所以把它拆掉什麼也換不到。
 */
export type Gate = 'editor' | 'error' | 'loading'

type GateInput = {
  /** 問卷與成員都有資料（可能是快取裡的舊資料） */
  loaded: boolean
  /** 問卷或成員最近一次讀取失敗 */
  failed: boolean
  /** 這次進房間之後，問卷與成員都讀過一次了（成功或失敗） */
  fetchedAfterMount: boolean
  /** 這張單的問卷已經打開過 */
  opened: boolean
}

export function gate({ loaded, failed, fetchedAfterMount, opened }: GateInput): Gate {
  if (opened && loaded) return 'editor'
  if (failed) return 'error'
  // 等這次進房間重新讀到的資料：用快取裡的舊版本開始編輯，第一次儲存就會版本衝突
  return loaded && fetchedAfterMount ? 'editor' : 'loading'
}
