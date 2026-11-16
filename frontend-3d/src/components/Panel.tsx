/**
 * 展開的面板（個人營養、收據明細、成員資料、設定…）。
 * 桌機與平板：浮在場景左邊或右邊的奶油色面板，不遮住場景中央；手機：從下方滑出的抽屜。
 * 版面由 <Room> 的 data-layout 決定，所以要放在 <Room hud={…}>（通常是 <SceneHud> 的 children）裡。
 *
 *   {open && <Panel title="收據明細" side="right" onClose={() => setOpen(false)}>…</Panel>}
 *
 * - 要顯示才掛上去（由外面用條件決定），關掉就卸下。
 * - 打開時焦點移進面板；關掉後回到原本的地方（通常是打開它的那個物件）。
 * - Escape 或右上角的 ✕ 會呼叫 onClose。約定：誰處理了 Escape 就呼叫 preventDefault()，其他浮動介面看到就不再處理。
 * - 內容太長會在面板裡上下捲動；footer 固定在底部（放 <Button>）。
 * - 它不是強制回應的對話框：桌機上場景仍可操作。需要使用者一定要回答時用 <ConfirmDialog>。
 */
import { useEffect, useId, useRef, type ReactNode } from 'react'
import styles from './Panel.module.css'

type PanelProps = {
  title: string
  /** 桌機放在哪一邊；預設右邊。手機一律是下方抽屜 */
  side?: 'left' | 'right'
  onClose: () => void
  children: ReactNode
  /** 固定在面板底部的動作 */
  footer?: ReactNode
  className?: string
}

// 舊一點的瀏覽器不認得 :modal，查詢會丟例外；退回用 open 屬性判斷
function modalIsOpen(): boolean {
  try {
    return document.querySelector('dialog:modal') !== null
  } catch {
    return document.querySelector('dialog[open]') !== null
  }
}

export function Panel({ title, side = 'right', onClose, children, footer, className }: PanelProps) {
  const panel = useRef<HTMLElement>(null)
  const titleId = useId()

  useEffect(() => {
    const previous = document.activeElement
    panel.current?.focus()
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus()
    }
  }, [])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) return
      // 上面還疊著強制回應的對話框時，Escape 是給它的
      if (modalIsOpen()) return
      // 同時開著兩個面板時，只關焦點所在的那一個
      const focused = document.activeElement?.closest('[data-panel]')
      if (focused && focused !== panel.current) return
      // 標成已處理：一下 Escape 只關一層。聊天室視窗、選單、其他面板的監聽都會先看 defaultPrevented，
      // 不然焦點在頁面空白處時，面板和聊天室會被同一下 Escape 一起關掉（填到一半的表單就沒了）
      event.preventDefault()
      onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  return (
    <section
      ref={panel} role="dialog" aria-labelledby={titleId} tabIndex={-1} data-panel data-no-swipe
      className={`${styles.panel} ${side === 'left' ? styles.left : styles.right} ${className ?? ''}`}
    >
      <header className={styles.header}>
        <h2 id={titleId}>{title}</h2>
        <button type="button" className={styles.close} aria-label={`關閉${title}`} onClick={onClose}>✕</button>
      </header>
      <div className={styles.body}>{children}</div>
      {footer && <footer className={styles.footer}>{footer}</footer>}
    </section>
  )
}
