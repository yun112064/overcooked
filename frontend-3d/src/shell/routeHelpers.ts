/** 路由用得到的純函式（App.tsx 與錯誤畫面共用）。 */

/** 舊前端在網址上帶日期的格式；真正的日期檢查由冰箱門自己做（不合法就當今天） */
const DAY_SHAPE = /^\d{4}-\d{2}-\d{2}$/

/**
 * 舊前端的 /history?date=…、/generation-history?date=… 要轉到冰箱門的哪個網址。
 * 書籤與分享過的連結多半帶著日期，轉過去時要一起帶，不然會落在今天。
 */
export function legacyRecordsPath(tab: 'adopted' | 'nutrition', date: string | null): string {
  const params = new URLSearchParams({ tab })
  if (date && DAY_SHAPE.test(date)) params.set('date', date)
  return `/records?${params.toString()}`
}

/** 各家瀏覽器（與 Vite 的預先載入）在「房間的程式檔沒抓到」時丟的訊息 */
const CHUNK_ERROR = /dynamically imported module|importing a module script failed|unable to preload css|loading chunk/i

/** 是不是「房間的程式檔沒載到」（網路不穩，或網站剛更新、舊檔名不在了）；這種重新整理就會好。 */
export function isChunkLoadError(error: unknown): boolean {
  return error instanceof Error && CHUNK_ERROR.test(error.message)
}

/** 載入失敗（網路閃了一下）就隔一會兒再試一次；第二次還是失敗才往外丟。 */
export function retryOnce<T>(load: () => Promise<T>, waitMs = 600): Promise<T> {
  return load().catch(() => new Promise<void>((resolve) => { setTimeout(resolve, waitMs) }).then(load))
}
