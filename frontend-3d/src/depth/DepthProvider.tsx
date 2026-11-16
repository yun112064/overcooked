import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  deviceStore, markMotionPrompted, readDepthMode, shouldPromptReducedMotion, writeDepthMode, type DepthMode,
} from './depthMode'
import styles from './DepthProvider.module.css'

type DepthContextValue = { mode: DepthMode; setMode: (mode: DepthMode) => void }

const DepthContext = createContext<DepthContextValue | null>(null)

// eslint-disable-next-line react-refresh/only-export-components
export function useDepth(): DepthContextValue {
  const value = useContext(DepthContext)
  if (!value) throw new Error('useDepth 必須在 <DepthProvider> 裡使用')
  return value
}

/**
 * 系統開了「減少動態」時只問一次的提示（DESIGN.md 3.6）。
 * 它是給鍵盤與讀屏使用者看的，所以一出現就把焦點放到第一顆按鈕並念出來，Escape＝維持現狀；
 * 不然它排在整個場景的後面，要 Tab 過整間房間才找得到。
 */
function MotionPrompt({ onAnswer }: { onAnswer: (next?: DepthMode) => void }) {
  const first = useRef<HTMLButtonElement>(null)
  const textId = useId()

  useEffect(() => {
    const previous = document.activeElement
    first.current?.focus()
    return () => {
      if (previous instanceof HTMLElement && previous !== document.body && previous.isConnected) previous.focus()
    }
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      // 標成已處理：同一下 Escape 不要再關掉後面的面板或聊天室
      event.preventDefault()
      onAnswer()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onAnswer])

  return (
    <div className={styles.prompt} role="alertdialog" aria-label="動態效果" aria-describedby={textId}>
      <p id={textId}>
        你的裝置開了「減少動態」，所以鏡頭移動和跟著游標的效果先停掉了。要不要把景深效果也固定成靜態？之後可以在設定改回來。
      </p>
      <div className={styles.actions}>
        <button ref={first} type="button" className={styles.primary} onClick={() => onAnswer('static')}>改成靜態景深</button>
        <button type="button" onClick={() => onAnswer()}>維持現狀</button>
      </div>
    </div>
  )
}

export function DepthProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<DepthMode>(() => readDepthMode(deviceStore()))
  const [prompt, setPrompt] = useState(() =>
    shouldPromptReducedMotion(
      deviceStore(),
      window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      readDepthMode(deviceStore()),
    ))

  const setMode = useCallback((next: DepthMode) => {
    setModeState(next)
    writeDepthMode(deviceStore(), next)
  }, [])

  // CSS 依 <html data-depth> 決定要不要動
  useEffect(() => {
    document.documentElement.dataset.depth = mode
  }, [mode])

  const answer = useCallback((next?: DepthMode) => {
    markMotionPrompted(deviceStore())
    setPrompt(false)
    if (next) setMode(next)
  }, [setMode])

  const value = useMemo(() => ({ mode, setMode }), [mode, setMode])
  return (
    <DepthContext.Provider value={value}>
      {children}
      {prompt && <MotionPrompt onAnswer={answer} />}
    </DepthContext.Provider>
  )
}
