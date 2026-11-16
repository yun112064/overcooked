/**
 * 固定在畫面上方中央的位置，用來放整個場景層級的訊息：載入中、讀取失敗、空狀態的引導、暫時的說明。
 * 放在 <Room hud={…}>（通常是 <SceneHud> 的 children）裡；不跟鏡頭移動，手機換停靠點時也看得到。
 *
 *   <SceneHud tab="plan">
 *     {list.isPending && <HudNote><Spinner label="正在看爐上有什麼…" /></HudNote>}
 *     {list.isError && <HudNote><ErrorNotice error={list.error} onRetry={() => list.refetch()} /></HudNote>}
 *   </SceneHud>
 *
 * 只負責位置；內容用 <Notice>、<ErrorNotice>、<Spinner>。
 */
import type { ReactNode } from 'react'
import styles from './HudNote.module.css'

export function HudNote({ children }: { children: ReactNode }) {
  return <div className={styles.note} data-no-swipe>{children}</div>
}
