/**
 * 吉祥物「小番茄主廚」（DESIGN.md 5.9）：維持插畫風，在畫面底部的跑道上來回跑。
 * 圖形與 src/assets/mascot/mascot.svg 相同，內嵌成元件才能用 CSS 讓手腳動起來。
 *
 * - <Mascot message="…" />：裝飾用（大門、玄關）。點它會停下來揮手，說這一句話。
 * - <MascotRunner … />：由外面控制的版本，飲食小幫手的視窗用它（src/assistant/AssistantLauncher.tsx）。
 * - <MascotFigure pose="…" />：只有圖，不會跑；對話裡的頭像用。
 *
 * 只有吉祥物本體可以點；跑道其他地方不擋場景的點擊。
 * 靜態景深、關閉模式與系統「減少動態」時不跑，站在右下角。
 */
import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type Ref } from 'react'
import styles from './Mascot.module.css'

/** 來回跑一圈的時間；和 Mascot.module.css 的 --patrol 是同一個數字 */
const PATROL_MS = 16000

type MascotFigureProps = {
  /** run 跑步、wave 站著揮手、think 原地小跳（查資料中）、still 不動；預設 still */
  pose?: 'run' | 'wave' | 'think' | 'still'
  className?: string
}

export function MascotFigure({ pose = 'still', className }: MascotFigureProps) {
  return (
    <span className={`${styles.figure} ${styles[pose] ?? ''} ${className ?? ''}`}>
      <svg viewBox="0 0 120 140" aria-hidden="true">
        <ellipse className={styles.shadow} cx="60" cy="133" rx="30" ry="5" fill="#1A1420" opacity=".3" />
        <g className={styles.legA}>
          <path d="M48 102 V122" stroke="#C23B21" strokeWidth="7" strokeLinecap="round" />
          <ellipse cx="52" cy="126" rx="11" ry="6" fill="#3A2A20" /><ellipse cx="55" cy="124" rx="4" ry="2" fill="#6E5A4C" />
        </g>
        <g className={styles.legB}>
          <path d="M72 102 V122" stroke="#C23B21" strokeWidth="7" strokeLinecap="round" />
          <ellipse cx="76" cy="126" rx="11" ry="6" fill="#3A2A20" /><ellipse cx="79" cy="124" rx="4" ry="2" fill="#6E5A4C" />
        </g>
        <g className={styles.bob}>
          <g className={styles.armB}>
            <path d="M94 76 q14 4 16 16" fill="none" stroke="#D9472B" strokeWidth="7" strokeLinecap="round" />
            <circle cx="110" cy="93" r="5.5" fill="#E8603F" />
          </g>
          <circle cx="60" cy="72" r="40" fill="#E8603F" />
          <path d="M24 88 a40 40 0 0 0 72 0 a44 44 0 0 1 -72 0Z" fill="#D9472B" />
          <ellipse cx="44" cy="52" rx="13" ry="8" fill="#F59C82" opacity=".85" transform="rotate(-28 44 52)" />
          <circle cx="47" cy="72" r="5" fill="#3A2A20" /><circle cx="73" cy="72" r="5" fill="#3A2A20" />
          <circle cx="48.5" cy="70.5" r="1.7" fill="#fff" /><circle cx="74.5" cy="70.5" r="1.7" fill="#fff" />
          <ellipse cx="37" cy="83" rx="6" ry="4" fill="#F59C82" opacity=".9" /><ellipse cx="83" cy="83" rx="6" ry="4" fill="#F59C82" opacity=".9" />
          <path d="M52 84 q8 8 16 0" fill="none" stroke="#3A2A20" strokeWidth="3" strokeLinecap="round" />
          <path d="M60 38 l-16 -5 l11 -5 l-3 -11 l8 8 l8 -8 l-3 11 l11 5Z" fill="#3F7D4E" />
          <circle cx="46" cy="16" r="11" fill="#FFFDF7" /><circle cx="60" cy="11" r="13" fill="#FFFDF7" /><circle cx="74" cy="16" r="11" fill="#FFFDF7" />
          <rect x="42" y="18" width="36" height="13" rx="4" fill="#FFFDF7" /><rect x="42" y="27" width="36" height="4" rx="2" fill="#F3E0C3" />
          <g className={styles.armA}>
            <path d="M26 76 q-14 4 -16 16" fill="none" stroke="#E8603F" strokeWidth="7" strokeLinecap="round" />
            <circle cx="10" cy="93" r="5.5" fill="#F07352" />
          </g>
        </g>
      </svg>
    </span>
  )
}

type MascotRunnerProps = {
  /** 按鈕給讀屏的名稱 */
  label: string
  /** 停下來揮手（true）還是繼續跑 */
  stopped: boolean
  /** 有開合的視窗時給它，會標 aria-expanded */
  expanded?: boolean
  /** 頭上的對話泡泡；null 就不顯示 */
  bubble?: string | null
  /** 右上角的未讀小圓點 */
  unread?: boolean
  /** 不來回跑，固定站在右下角（場景的主要按鈕就在跑道上時用） */
  parked?: boolean
  /** 一開始站在一圈的哪裡（毫秒）；沒給就照現在的時間算，換房間時才不會每次都從左邊重新起跑 */
  phase?: number | null
  /** phase：被點到時跑到一圈的哪裡；沒在跑（靜態景深等）時是 null */
  onClick: (phase: number | null) => void
  buttonRef?: Ref<HTMLButtonElement>
  laneRef?: Ref<HTMLDivElement>
  /** 放在跑道裡、不跟著吉祥物跑的東西（聊天室視窗） */
  children?: ReactNode
}

export function MascotRunner({
  label, stopped, expanded, bubble = null, unread = false, parked = false, phase, onClick, buttonRef, laneRef, children,
}: MascotRunnerProps) {
  const runner = useRef<HTMLDivElement>(null)
  const [start] = useState(() => phase ?? performance.now() % PATROL_MS)

  const click = () => {
    // 問瀏覽器動畫現在跑到哪，停下來時才記得位置；問不到（舊瀏覽器、沒在跑）就給 null
    const progress = runner.current?.getAnimations?.()[0]?.effect?.getComputedTiming().progress
    onClick(typeof progress === 'number' ? progress * PATROL_MS : null)
  }

  return (
    <div
      ref={laneRef} className={`${styles.lane} ${stopped ? styles.stopped : ''} ${parked ? styles.parked : ''}`}
      style={{ '--phase': `${-start}ms` } as CSSProperties}
    >
      {children}
      <div ref={runner} className={styles.runner}>
        {/* 泡泡一直掛著（沒話說時藏起來）：它的左右位移動畫要和跑步同時開始，才不會在畫面邊緣被切掉 */}
        <p className={`${styles.bubble} ${bubble ? '' : styles.bubbleEmpty}`} role="status">{bubble}</p>
        <button
          ref={buttonRef} type="button" className={styles.body} aria-label={label} aria-expanded={expanded} onClick={click}
        >
          <span className={styles.flip}><MascotFigure pose={stopped ? 'wave' : parked ? 'still' : 'run'} /></span>
          {unread && <span className={styles.unread} aria-hidden="true" />}
        </button>
      </div>
    </div>
  )
}

type MascotProps = {
  /** 點它時說的話 */
  message: string
}

/** 裝飾用的吉祥物：還不能問問題的地方（大門、玄關）用。 */
export function Mascot({ message }: MascotProps) {
  const [talking, setTalking] = useState(false)

  useEffect(() => {
    if (!talking) return
    const timer = window.setTimeout(() => setTalking(false), 4000)
    return () => window.clearTimeout(timer)
  }, [talking])

  return (
    <MascotRunner
      label="飲食小幫手" stopped={talking} bubble={talking ? message : null} onClick={() => setTalking((v) => !v)}
    />
  )
}
