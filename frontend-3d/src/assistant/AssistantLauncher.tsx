/**
 * 飲食小幫手的入口（DESIGN.md 5.9）：畫面底部來回跑的吉祥物，點它在身旁打開懸浮的聊天室視窗。
 *
 * - 具名匯出 AssistantLauncher（<SceneHud> 以這個名字載入）；唯一的 prop 是 parked（吉祥物站在右下角不跑）。
 * - 對話內容、視窗開著沒、看到哪一則，都放在 AssistantProvider；這個元件每換一個房間就重新掛載，自己不留狀態。
 * - 視窗三種樣子：關著（吉祥物繼續跑）、打開、縮成一條標題列（對話照常進行，可以先看後面的房間）。
 * - 手機上換房間時，開著的視窗會先縮成標題列，不然新的房間整個被蓋住。
 * - 沒有包在 AssistantProvider 裡（還沒進屋）時，吉祥物只是裝飾。
 */
import {
  useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent,
} from 'react'
import { useNavigate } from 'react-router'
import { useRoom } from '../camera/Room'
import { clamp } from '../camera/framing'
import { useDepth } from '../depth/DepthProvider'
import { Mascot, MascotFigure, MascotRunner } from '../shell/Mascot'
import { ChatLog, Composer, EngineSwitch } from './ChatParts'
import { placeWindow, settledId } from './chatLogic'
import { AssistantChatContext, type AssistantChat, type LauncherState } from './chatStore'
import styles from './AssistantLauncher.module.css'

/** 一開始打招呼多久 */
const HINT_MS = 7000
const NO_DRAG = { x: 0, y: 0 }

type LauncherProps = {
  /** 吉祥物不來回跑，固定站在右下角：場景的主要按鈕就在底部跑道上時用（見 SceneHud 的 assistant="parked"） */
  parked?: boolean
}

export function AssistantLauncher({ parked = false }: LauncherProps) {
  const chat = useContext(AssistantChatContext)
  if (!chat) return <Mascot message="開門後就能問我" />
  return <Launcher chat={chat} parked={parked} />
}

export default AssistantLauncher

function Launcher({ chat, parked }: { chat: AssistantChat; parked: boolean }) {
  const { phone } = useRoom()
  const { mode } = useDepth()
  const navigate = useNavigate()
  const { launcher, setLauncher } = chat
  const { view } = launcher
  const open = view !== 'closed'

  const lane = useRef<HTMLDivElement>(null)
  const mascot = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  /** 使用者剛親手打開視窗：焦點要移進去。換房間後自己恢復的不搶焦點 */
  const focusOnOpen = useRef(false)
  /** 掛載時視窗就已經開著＝剛換了房間 */
  const restored = useRef(view === 'open')
  const dragFrom = useRef<{ x: number; y: number; left: number; right: number; top: number } | null>(null)
  const [spot, setSpot] = useState<{ left: number; tail: number } | null>(null)
  const [drag, setDrag] = useState(NO_DRAG)

  const settled = settledId(chat.messages)
  // 回答還在生成時 settled 是 0；已讀只往前不往回
  const seen = Math.max(settled, launcher.seenId)
  const failed = chat.messages.at(-1)?.status === 'error'
  const unread = view !== 'open' && settled > launcher.seenId
  const news = failed ? '這題沒答成，點我看看' : '回答好了！'

  /** 開關視窗時順便記下已讀；phase 是吉祥物停下來的位置 */
  const show = useCallback((next: LauncherState['view'], phase?: number | null) => {
    setLauncher({ view: next, seenId: seen, greeted: true, ...(phase === undefined ? {} : { phase }) })
    if (next === 'closed') {
      setSpot(null)
      setDrag(NO_DRAG)
    }
  }, [setLauncher, seen])

  const close = useCallback(() => {
    // 焦點原本在視窗裡的話還給吉祥物，不然鍵盤使用者會不知道自己在哪
    if (panel.current?.contains(document.activeElement)) mascot.current?.focus()
    show('closed')
  }, [show])

  // 打招呼幾秒就好；每次進屋只說一次
  useEffect(() => {
    if (launcher.greeted) return
    const timer = window.setTimeout(() => setLauncher({ greeted: true }), HINT_MS)
    return () => window.clearTimeout(timer)
  }, [launcher.greeted, setLauncher])

  // 手機換房間：開著的視窗先縮成標題列（已經在視窗裡看過的回答算已讀）
  useEffect(() => {
    if (restored.current && phone) setLauncher({ view: 'mini', seenId: seen })
    restored.current = false
  }, [phone, setLauncher, seen])

  // Escape 關閉。焦點在別的東西上（場景的面板、選單、對話框）時那個 Escape 是給它們的
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      // 注音選字中的 Escape 是取消選字
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return
      const active = document.activeElement
      const mine = !active || active === document.body || active === mascot.current || panel.current?.contains(active)
      if (!mine) return
      // 標成已處理（和 <Panel> 同一個約定）：同一下 Escape 不要連場景的面板一起關
      event.preventDefault()
      close()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, close])

  // 視窗擺在吉祥物旁邊：量吉祥物停在哪，算出視窗的左緣與尖角的位置
  useLayoutEffect(() => {
    if (!open) return
    const measure = () => {
      const track = lane.current
      const button = mascot.current
      const element = panel.current
      if (!track || !button || !element) return
      const trackBox = track.getBoundingClientRect()
      const box = button.getBoundingClientRect()
      const leftInset = parseFloat(getComputedStyle(track).getPropertyValue('--left-inset')) || 0
      const next = placeWindow({
        anchor: box.left + box.width / 2 - trackBox.left, lane: trackBox.width, width: element.offsetWidth, leftInset,
      })
      setSpot((prev) => (prev && prev.left === next.left && prev.tail === next.tail ? prev : next))
    }
    measure()
    // 切換景深模式時吉祥物會換位置，但 <html data-depth> 要到這一輪畫完才更新，所以下一格再量一次
    const frame = requestAnimationFrame(measure)
    const onResize = () => {
      setDrag(NO_DRAG)
      measure()
    }
    window.addEventListener('resize', onResize)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', onResize)
    }
  }, [open, mode, phone])

  useLayoutEffect(() => {
    if (view !== 'open' || !focusOnOpen.current) return
    focusOnOpen.current = false
    // 手機先不叫出鍵盤（會把剛打開的視窗遮掉一半），焦點放在視窗本身
    const target = phone ? panel.current : input.current
    target?.focus({ preventScroll: true })
  }, [view, phone])

  const onMascotClick = (phase: number | null) => {
    if (view === 'open') {
      show('closed')
      return
    }
    focusOnOpen.current = true
    // 從標題列還原時吉祥物本來就停著，位置不用重記
    show('open', view === 'closed' ? phase : undefined)
  }

  // ---- 拖曳（桌機與平板）：抓標題列移動視窗，不能拖出畫面 ----
  const onDragStart = (event: PointerEvent<HTMLElement>) => {
    if (phone || event.button !== 0 || (event.target as Element).closest('button')) return
    const box = panel.current?.getBoundingClientRect()
    if (!box) return
    dragFrom.current = {
      x: event.clientX - drag.x, y: event.clientY - drag.y,
      left: box.left - drag.x, right: box.right - drag.x, top: box.top - drag.y,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const onDragMove = (event: PointerEvent<HTMLElement>) => {
    const from = dragFrom.current
    if (!from) return
    setDrag({
      x: clamp(event.clientX - from.x, 8 - from.left, window.innerWidth - 8 - from.right),
      // 標題列至少留在畫面裡，才抓得回來
      y: clamp(event.clientY - from.y, 8 - from.top, window.innerHeight - 56 - from.top),
    })
  }
  const onDragEnd = () => { dragFrom.current = null }

  const dragged = drag.x !== 0 || drag.y !== 0
  const style = {
    left: spot?.left ?? 0,
    '--tail': `${spot?.tail ?? 0}px`,
    transform: dragged ? `translate(${drag.x}px, ${drag.y}px)` : undefined,
    // 還沒量到位置前先不顯示，免得在左邊閃一下
    visibility: spot ? undefined : 'hidden',
  } as CSSProperties

  return (
    <MascotRunner
      laneRef={lane} buttonRef={mascot} stopped={open} expanded={view === 'open'} parked={parked}
      // 停著的時候站回上次停的地方；在跑的話照時間算位置就好（parked 時固定在右下角，不看這個）
      phase={open ? launcher.phase : null}
      label={view === 'open' ? '關閉飲食小幫手' : '開啟飲食小幫手，問飲食問題'}
      unread={unread}
      bubble={open ? null : chat.busy ? '查資料中…' : unread ? news : launcher.greeted ? null : '有飲食問題問我！'}
      onClick={onMascotClick}
    >
      {open && (
        <section
          ref={panel} role="dialog" aria-label="飲食小幫手" tabIndex={-1} data-panel data-no-swipe
          className={`${styles.window} ${view === 'mini' ? styles.mini : ''}`} style={style}
        >
          <header
            className={styles.header}
            onPointerDown={onDragStart} onPointerMove={onDragMove} onPointerUp={onDragEnd} onPointerCancel={onDragEnd}
          >
            <MascotFigure className={styles.avatar} pose={chat.busy ? 'think' : 'still'} />
            <div className={styles.title}>
              <h2>飲食小幫手</h2>
              <p role={view === 'mini' ? 'status' : undefined}>
                {view === 'open' ? '回答都附上出處' : chat.busy ? '查資料中…' : unread ? news : '對話先收起來了'}
              </p>
            </div>
            {view === 'open' && chat.messages.length > 0 && (
              <button type="button" className={styles.icon} aria-label="重新開始對話" title="重新開始" onClick={chat.reset}>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12a7 7 0 1 0 2.2-5.1M5 4.5v3.2h3.2" /></svg>
              </button>
            )}
            <button
              type="button" className={styles.expand} title="展開成完整頁面"
              onClick={() => { show('closed'); navigate('/assistant') }}
            >
              展開<span className="visually-hidden">成完整頁面</span>
            </button>
            <button
              type="button" className={styles.icon} onClick={() => show(view === 'mini' ? 'open' : 'mini')}
              aria-label={view === 'mini' ? '還原視窗' : '縮小視窗'} title={view === 'mini' ? '還原' : '縮小'}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                {view === 'mini' ? <path d="M6 15l6-6 6 6" /> : <path d="M6 12h12" />}
              </svg>
            </button>
            <button type="button" className={styles.icon} aria-label="關閉飲食小幫手" title="關閉" onClick={close}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11" /></svg>
            </button>
          </header>

          {/* 縮小時只藏起來不卸下：打到一半的問題還在 */}
          <div className={styles.body} hidden={view === 'mini'}>
            {chat.openaiAvailable && <div className={styles.engine}><EngineSwitch /></div>}
            <ChatLog scope="float" compact active={view === 'open'} className={styles.log} />
            <div className={styles.foot}>
              <Composer compact inputRef={input}>僅供一般飲食參考，不能取代醫師或營養師；緊急情況請撥 119。</Composer>
            </div>
          </div>

          {/* 指向吉祥物的小尖角；視窗被拖走後就不指了 */}
          {!dragged && <span className={styles.tail} aria-hidden="true" />}
        </section>
      )}
    </MascotRunner>
  )
}
