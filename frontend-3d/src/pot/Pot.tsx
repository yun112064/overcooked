/**
 * 鍋的圖。依 potState() 算出來的狀態挑對應的插圖（src/assets/pot），生成中疊上蒸氣、失敗疊上黑煙。
 *
 *   <Placed x={1328} y={388} width={84} k={0.15}>
 *     <Pot view="side" state={potState({ request, job })} />
 *   </Placed>
 *
 * - view：'side' 側視（客廳門口、廚房瓦斯爐）／'top' 俯視（單板旁）。
 * - 寬度跟著外層（外層要有明確的寬度，例如 <Placed width>、<Hotspot width>）；高度自己算。
 *   側視圖以鍋身 240×170 為基準：開蓋（ready）和失敗的圖比較大，會自動往上、往右超出這個框，
 *   所以換狀態時鍋身不會跳動或縮放。俯視圖一律是正方形。
 * - 預設是純裝飾（讀屏略過）。鍋本身就是唯一的狀態提示時傳 label，例如 label={potLabel(state)}；
 *   放在 <Hotspot> 裡時由 Hotspot 的 label 說明就好。
 * - 蒸氣與黑煙是 CSS 動畫；靜態景深、關閉、系統「減少動態」時不動，只留靜止的幾縷。
 * - 俯視的料（菜→菇→肉→湯→配菜）是固定的進度記號，和使用者填的內容無關。
 */
import type { CSSProperties } from 'react'
import sideFailed from '../assets/pot/pot-side-failed.svg'
import sideFilled from '../assets/pot/pot-side-filled.svg'
import sideLidded from '../assets/pot/pot-side-lidded.svg'
import sideReady from '../assets/pot/pot-side-ready.svg'
import sideEmpty from '../assets/pot/pot-side.svg'
import top0 from '../assets/pot/pot-top-0.svg'
import top1 from '../assets/pot/pot-top-1.svg'
import top2 from '../assets/pot/pot-top-2.svg'
import top3 from '../assets/pot/pot-top-3.svg'
import top4 from '../assets/pot/pot-top-4.svg'
import top5 from '../assets/pot/pot-top-5.svg'
import topFailed from '../assets/pot/pot-top-failed.svg'
import topLidded from '../assets/pot/pot-top-lidded.svg'
import topReady from '../assets/pot/pot-top-ready.svg'
import type { PotKind, PotState } from './potState'
import styles from './Pot.module.css'

/** 圖檔與它的 viewBox（minX, minY, 寬, 高）；用來把不同大小的圖對齊到同一個鍋身上 */
type Art = { src: string; box: readonly [number, number, number, number] }

const SIDE_W = 240
const SIDE_H = 170
const SIDE_BOX = [0, 0, SIDE_W, SIDE_H] as const
// viewBox 要和各個 SVG 檔案開頭寫的一致
const SIDE: Record<PotKind, Art> = {
  empty: { src: sideEmpty, box: SIDE_BOX },
  filling: { src: sideFilled, box: SIDE_BOX },
  full: { src: sideFilled, box: SIDE_BOX },
  cooking: { src: sideLidded, box: SIDE_BOX },
  ready: { src: sideReady, box: [0, -60, 300, 230] },
  failed: { src: sideFailed, box: [0, -20, 240, 190] },
}

const TOP_FILLING = [top0, top1, top2, top3, top4] as const

function topSrc(state: PotState): string {
  switch (state.kind) {
    case 'empty': return top0
    case 'filling': return TOP_FILLING[state.steps]
    case 'full': return top5
    case 'cooking': return topLidded
    case 'ready': return topReady
    case 'failed': return topFailed
  }
}

const percent = (value: number) => `${Math.round(value * 1e4) / 100}%`

function sideStyle({ box: [x, y, w, h] }: Art): CSSProperties {
  return { left: percent(x / SIDE_W), top: percent(y / SIDE_H), width: percent(w / SIDE_W), height: percent(h / SIDE_H) }
}

type PotProps = {
  view: 'side' | 'top'
  state: PotState
  /** 給讀屏的說明；不傳就當成裝飾 */
  label?: string
  className?: string
}

export function Pot({ view, state, label, className }: PotProps) {
  const side = view === 'side' ? SIDE[state.kind] : null
  return (
    <div
      className={`${styles.pot} ${view === 'side' ? styles.side : styles.top} ${className ?? ''}`}
      role={label ? 'img' : undefined} aria-label={label} data-pot={state.kind}
    >
      {side
        ? <img className={styles.art} style={sideStyle(side)} src={side.src} alt="" draggable={false} />
        : <img className={`${styles.art} ${styles.fill}`} src={topSrc(state)} alt="" draggable={false} />}
      {state.kind === 'cooking' && <span className={styles.steam} aria-hidden="true"><i /><i /><i /></span>}
      {state.kind === 'failed' && <span className={`${styles.steam} ${styles.smoke}`} aria-hidden="true"><i /><i /><i /></span>}
    </div>
  )
}
