/**
 * 房間元件（DESIGN.md 4.4、7.3）。每個場景用一個 <Room> 包起來。
 *
 * 公開的東西：
 * - <Room name layers stops hotspots hud bottomInset …>：世界（跟著鏡頭動）＋ HUD（固定在畫面上）。
 * - <Placed x y width k>：把不可點的東西放到畫稿座標（1600×1000；x＝水平中心、y＝上緣）。
 * - <Hotspot id label …>：世界裡可以點的物件；id 要和 hotspots 清單裡的同一個物件一致。
 * - useRoom()：{ phone, stopId, goToStop, flashId, reserveHud, view, frame, visible }。只能在 <Room> 裡（children 或 hud）使用。
 *   view／frame／visible 是現在的畫面大小、世界擺在哪、看得到畫稿的哪一塊；需要避開被裁掉的上下緣時用，不用自己量視窗。
 *
 * 手機規則：每個可點物件都要列在 hotspots，並指定它在哪個停靠點看得到。
 * 手機上這份清單是底部的物件列；桌機上平常視覺隱藏（給讀屏），鍵盤走到它時整列浮出來，焦點不會落在看不見的按鈕上。
 * 世界裡的物件暫時不能按（disabled）時，清單裡的那一顆也要標 disabled，不然同一個動作會從物件列再送一次。
 *
 * 房間自己會做的事（場景不用管）：
 * - 分頁標題改成「房間名｜今天吃啥」，換房間、換停靠點時念給讀屏聽。
 * - 鍵盤焦點落在別的停靠點才看得到的東西上時，鏡頭跟過去。
 * - 系統開了「減少動態」時鏡頭直接換位、不做視差。
 *
 * 底部的空間：HUD（分頁或動作列）透過 reserveHud 告訴房間要讓出多少，物件列與吉祥物跑道會自動往上移。
 * 場景自己在底部放東西（例如問卷抽屜的把手）時用 bottomInset 再多讓一些。
 *
 * 寫樣式時可以用祖先選擇器分辨版面：:global([data-layout='phone']) .x { … }（另一個值是 'wide'）。
 * 可用的 CSS 變數：--bottom-inset、--left-inset（px，已含 HUD 佔掉的空間）。
 */
import {
  createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState,
  type CSSProperties, type FocusEvent, type PointerEvent, type ReactNode,
} from 'react'
import { useDepth } from '../depth/DepthProvider'
import { announce } from './announce'
import { activeStops, frameFor, isPhone, place, stopShowing, visibleArt, type Frame, type Stop } from './framing'
import styles from './Room.module.css'

/** 物件列的一顆按鈕＝房間裡一個可點的東西（DESIGN.md 4.4）。 */
export type HotspotItem = {
  id: string
  label: string
  /** 顯示在名稱旁的狀態，例如「生成中」「3 張單」 */
  status?: string
  /** 這個物件在哪個停靠點看得到 */
  stop: string
  onActivate?: () => void
  /** 現在不能按（送出中、正在離開房間）；世界裡對應的 <Hotspot disabled> 是同一個條件 */
  disabled?: boolean
}

/** HUD 佔掉的畫面邊緣（px）。bottom：分頁或動作列；left：桌機的側欄。 */
export type HudInsets = { bottom?: number; left?: number }

type RoomContextValue = {
  /** 剛從物件列被點到、正在閃一下的物件 */
  flashId: string | null
  /** 手機直式（一次只看得到世界的一段，底部有物件列） */
  phone: boolean
  stopId: string
  goToStop: (id: string) => void
  /** HUD 用：告訴房間底部／左邊被佔掉多少，物件列、吉祥物、邊緣提示會讓開。傳 {} 表示不佔。 */
  reserveHud: (insets: HudInsets) => void
  /** 房間在畫面上的大小（px） */
  view: { w: number; h: number }
  /** 世界現在放多大、擺在哪（px） */
  frame: Frame
  /** 現在看得到畫稿的哪一塊（畫稿單位，和 <Placed> 的座標同一套） */
  visible: { left: number; right: number; top: number; bottom: number }
}

const RoomContext = createContext<RoomContextValue | null>(null)

// eslint-disable-next-line react-refresh/only-export-components
export function useRoom(): RoomContextValue {
  const value = useContext(RoomContext)
  if (!value) throw new Error('useRoom 必須在 <Room> 裡使用')
  return value
}

/** 各層跟著指標移動的比例：z0 遠景最少，z3 前景最多。 */
const PARALLAX = [0.15, 0.4, 0.7, 1]
const SWIPE_PX = 36
const FLASH_MS = 900
/** 物件列自己的高度加上它和下方的間距；吉祥物跑道要再往上讓這麼多 */
const DOCK_LANE_PX = 64
const APP_NAME = '今天吃啥'
/** 雙指放大超過這個倍率就當成「放大看」：這時橫向拖曳是在移動放大的畫面，不是換停靠點 */
const ZOOMED_SCALE = 1.01

let reducedMotionQuery: MediaQueryList | null = null
/** 系統的「減少動態」。查詢物件留著重用：滑鼠每動一下都會問一次。 */
function prefersReducedMotion(): boolean {
  if (typeof window.matchMedia !== 'function') return false
  reducedMotionQuery ??= window.matchMedia('(prefers-reduced-motion: reduce)')
  return reducedMotionQuery.matches
}

/** 焦點是不是鍵盤給的（會畫焦點框的那種）。很舊的瀏覽器不認得這個選擇器，當成不是。 */
function keyboardFocused(element: Element): boolean {
  try {
    return element.matches(':focus-visible')
  } catch {
    return false
  }
}

type RoomProps = {
  /** 給讀屏用的房間名稱 */
  name: string
  /** z0…z3 的圖檔網址，由遠到近 */
  layers: readonly string[]
  stops: readonly Stop[]
  /** 進房間時先停在哪個停靠點；預設第一個 */
  initialStop?: string
  /** 由外面控制目前的停靠點（例如選完家庭後把鏡頭帶到內門） */
  stopId?: string
  onStopChange?: (id: string) => void
  hotspots?: readonly HotspotItem[]
  /**
   * 手機上左右兩側的「‹ 上一段／下一段 ›」按鈕；預設顯示。
   * 場景自己在世界裡有切換停靠點的東西（例如冰箱門上的兩顆磁鐵）而且側邊按鈕會蓋到內容時關掉；
   * 滑動、物件列與上方的圓點照常可用。
   */
  edgeNav?: boolean
  /**
   * 手機直式時側邊按鈕離畫面上緣多遠（CSS 長度）；預設在畫面 42% 高的地方。
   * 那個高度剛好是場景的內容時，改到場景確定空著的那一條（例如冰箱門的磁鐵那一排）。
   */
  edgeTop?: string
  /** 場景自己在畫面底部佔掉的高度（px），會加在 HUD 佔掉的高度上；預設 0 */
  bottomInset?: number
  /** 疊在世界裡、跟著鏡頭移動的東西（用 <Placed>、<Hotspot> 以畫稿座標定位） */
  children?: ReactNode
  /** 固定在畫面上、不跟鏡頭移動的東西（通常是 <SceneHud>，面板與對話框也放這裡） */
  hud?: ReactNode
}

// 在這些東西上按下不算滑動換停靠點（對話框、面板、表單元件都要能正常拖曳與選字）
function isInteractive(target: EventTarget | null): boolean {
  return target instanceof Element
    && target.closest('button, input, select, textarea, a, label, dialog, [data-no-swipe]') !== null
}

export function Room({
  name, layers, stops, initialStop, stopId, onStopChange, hotspots, edgeNav = true, edgeTop, bottomInset = 0, children, hud,
}: RoomProps) {
  const { mode } = useDepth()
  const viewport = useRef<HTMLDivElement>(null)
  const world = useRef<HTMLDivElement>(null)
  const [zoomed, setZoomed] = useState(false)
  const [size, setSize] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }))
  const [ownStop, setOwnStop] = useState(initialStop ?? stops[0].id)
  const [flashId, setFlashId] = useState<string | null>(null)
  const [hudBottom, setHudBottom] = useState(0)
  const [hudLeft, setHudLeft] = useState(0)
  const swipeStart = useRef<number | null>(null)
  const parallaxFrame = useRef(0)

  useLayoutEffect(() => {
    const element = viewport.current
    if (!element) return
    // 量到 0（樣式還沒套上、分頁在背景）就沿用上一次的尺寸，不讓世界縮成一個點
    const measure = () => {
      const w = element.clientWidth
      const h = element.clientHeight
      if (w > 0 && h > 0) setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    window.addEventListener('resize', measure)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [])

  useEffect(() => {
    if (!flashId) return
    const timer = window.setTimeout(() => setFlashId(null), FLASH_MS)
    return () => window.clearTimeout(timer)
  }, [flashId])

  // 雙指放大看小字的時候（.viewport 的 touch-action 允許 pinch-zoom），橫向拖曳要留給瀏覽器移動放大的畫面
  useEffect(() => {
    const visual = window.visualViewport
    if (!visual) return
    const update = () => setZoomed(visual.scale > ZOOMED_SCALE)
    update()
    visual.addEventListener('resize', update)
    return () => visual.removeEventListener('resize', update)
  }, [])

  // 每個房間的分頁標題不一樣（瀏覽紀錄、分頁列才分得出來），換房間時也念給讀屏聽
  useEffect(() => {
    document.title = `${name}｜${APP_NAME}`
    announce(name)
  }, [name])

  const available = activeStops(stops, size.w, size.h)
  const wanted = stopId ?? ownStop
  const current = available.find((s) => s.id === wanted) ?? available[0]
  const index = available.indexOf(current)
  const currentId = current.id
  const currentLabel = current.label
  const phone = isPhone(size.w, size.h)
  const frame = frameFor(size.w, size.h, current)

  // 鏡頭換了停靠點：念出新的那一段（剛進房間不念，上面已經念過房間名）
  const announcedStop = useRef(currentId)
  useEffect(() => {
    if (announcedStop.current === currentId) return
    announcedStop.current = currentId
    announce(`${name}：${currentLabel}`)
  }, [name, currentId, currentLabel])

  const goToStop = useCallback((id: string) => {
    setOwnStop(id)
    onStopChange?.(id)
  }, [onStopChange])

  const reserveHud = useCallback((insets: HudInsets) => {
    setHudBottom(insets.bottom ?? 0)
    setHudLeft(insets.left ?? 0)
  }, [])

  const step = (direction: 1 | -1) => {
    const next = available[index + direction]
    if (next) goToStop(next.id)
  }

  const onPointerDown = (event: PointerEvent) => {
    swipeStart.current = zoomed || isInteractive(event.target) ? null : event.clientX
  }
  const onPointerUp = (event: PointerEvent) => {
    if (swipeStart.current === null) return
    const dx = event.clientX - swipeStart.current
    swipeStart.current = null
    if (Math.abs(dx) > SWIPE_PX) step(dx < 0 ? 1 : -1)
  }
  // 滑鼠移動時各層依遠近位移；只在沉浸模式，且不處理觸控（手機之後由陀螺儀負責）。
  // 系統開了減少動態就不做：景深設定不會自動改，但還沒回答提示的人也不該先被晃到
  const onPointerMove = (event: PointerEvent) => {
    if (mode !== 'immersive' || event.pointerType !== 'mouse' || prefersReducedMotion()) return
    const element = viewport.current
    if (!element) return
    const px = (event.clientX / size.w - 0.5) * 2
    const py = (event.clientY / size.h - 0.5) * 2
    cancelAnimationFrame(parallaxFrame.current)
    parallaxFrame.current = requestAnimationFrame(() => {
      element.style.setProperty('--px', px.toFixed(3))
      element.style.setProperty('--py', py.toFixed(3))
    })
  }
  const resetParallax = () => {
    cancelAnimationFrame(parallaxFrame.current)
    viewport.current?.style.setProperty('--px', '0')
    viewport.current?.style.setProperty('--py', '0')
  }

  const activate = (item: HotspotItem) => {
    if (item.disabled) return
    // 不論要不要轉鏡頭都閃一下：有些物件的動作只是把焦點帶過去，不閃的話看起來像沒反應
    setFlashId(item.id)
    if (available.some((s) => s.id === item.stop) && item.stop !== current.id) {
      goToStop(item.stop)
      return
    }
    item.onActivate?.()
  }

  // 鍵盤焦點落在目前看不全的東西上（它在別的停靠點）：鏡頭跟過去。畫面是 overflow: clip，瀏覽器不會自己捲
  const onWorldFocus = (event: FocusEvent<HTMLDivElement>) => {
    if (available.length < 2 || !keyboardFocused(event.target)) return
    const box = world.current?.getBoundingClientRect()
    if (!box || box.width === 0) return
    const target = event.target.getBoundingClientRect()
    const next = stopShowing(
      available, size.w, size.h, currentId, (target.left - box.left) / box.width, (target.right - box.left) / box.width,
    )
    if (next !== currentId) goToStop(next)
  }

  const context: RoomContextValue = {
    flashId, phone, stopId: currentId, goToStop, reserveHud,
    view: size, frame, visible: visibleArt(size.w, size.h, frame),
  }
  const worldStyle: CSSProperties = {
    width: frame.width,
    height: frame.height,
    transform: `translate3d(${frame.left}px, ${frame.top}px, 0)`,
  }
  const previous = available[index - 1]
  const next = available[index + 1]
  const showDock = phone && !!hotspots && hotspots.length > 0
  const inset = bottomInset + hudBottom
  const viewportStyle = {
    '--bottom-inset': `${inset}px`,
    '--left-inset': `${hudLeft}px`,
    // 手機上有物件列時，吉祥物的跑道再往上讓開
    ...(showDock ? { '--mascot-inset': `${inset + DOCK_LANE_PX}px` } : {}),
    ...(phone && edgeTop ? { '--edge-top': edgeTop } : {}),
  } as CSSProperties

  return (
    <RoomContext.Provider value={context}>
      <div
        ref={viewport} className={styles.viewport} role="region" aria-label={name}
        data-layout={phone ? 'phone' : 'wide'} data-zoomed={zoomed || undefined} style={viewportStyle}
        onPointerDown={onPointerDown} onPointerUp={onPointerUp}
        onPointerCancel={() => { swipeStart.current = null }}
        onPointerMove={onPointerMove} onPointerLeave={resetParallax}
      >
        <div ref={world} className={styles.world} style={worldStyle} onFocus={onWorldFocus}>
          {layers.map((src, i) => (
            <img key={src} className={styles.layer} style={{ '--k': PARALLAX[i] ?? 1 } as CSSProperties} src={src} alt="" draggable={false} />
          ))}
          {children}
        </div>

        {available.length > 1 && (
          <>
            <div className={styles.dots} aria-hidden="true">
              {available.map((s) => <i key={s.id} className={s.id === current.id ? styles.dotOn : undefined} />)}
            </div>
            {edgeNav && previous && (
              <button type="button" className={`${styles.edge} ${styles.edgeLeft}`} onClick={() => step(-1)}>
                <span aria-hidden="true">‹ </span>{previous.label}
              </button>
            )}
            {edgeNav && next && (
              <button type="button" className={`${styles.edge} ${styles.edgeRight}`} onClick={() => step(1)}>
                {next.label}<span aria-hidden="true"> ›</span>
              </button>
            )}
          </>
        )}

        {hotspots && hotspots.length > 0 && (
          <nav className={showDock ? styles.dock : 'visually-hidden'} aria-label={`${name}裡可以操作的東西`} data-no-swipe data-dock>
            {hotspots.map((item) => (
              <button
                key={item.id} type="button"
                className={item.stop === current.id || available.length === 1 ? styles.dockHere : undefined}
                // 哪幾顆是目前這一段的，不能只靠橘框；aria-disabled 而不是 disabled：按到一半變成不能按時焦點不會掉
                aria-current={available.length > 1 && item.stop === current.id ? 'location' : undefined}
                aria-disabled={item.disabled || undefined}
                onClick={() => activate(item)}
              >
                {item.label}{item.status && <small>{item.status}</small>}
              </button>
            ))}
          </nav>
        )}
        {hud}
      </div>
    </RoomContext.Provider>
  )
}

type PlacedProps = {
  /** 畫稿座標（1600×1000）：x 是水平中心、y 是上緣 */
  x: number
  y: number
  width?: number
  /** 跟著指標移動的比例，應該和它所在的那一層一致 */
  k?: number
  className?: string
  style?: CSSProperties
  children?: ReactNode
}

/** 把東西放進世界裡的某個位置（不可點）。 */
export function Placed({ x, y, width, k = 0.7, className, style, children }: PlacedProps) {
  return (
    <div className={`${styles.placed} ${className ?? ''}`} style={{ ...place(x, y, width), '--k': k, ...style } as CSSProperties}>
      {children}
    </div>
  )
}

type HotspotProps = PlacedProps & {
  /** 和物件列裡同一個物件的 id 對應，被點到時會閃一下 */
  id: string
  label: string
  onClick?: () => void
  disabled?: boolean
  /** 這個物件開著面板、或目前被選取時標出來，讀屏才知道狀態（不只是畫面上多一圈） */
  'aria-expanded'?: boolean
  'aria-pressed'?: boolean
  'aria-current'?: boolean | 'page' | 'step' | 'date' | 'true'
  'aria-describedby'?: string
}

/** 世界裡一個可以點的物件。 */
export function Hotspot({ id, label, x, y, width, k = 0.7, className, style, onClick, disabled, children, ...aria }: HotspotProps) {
  const { flashId } = useRoom()
  return (
    <button
      type="button" aria-label={label} disabled={disabled} onClick={onClick} {...aria}
      className={`${styles.placed} ${styles.hotspot} ${flashId === id ? styles.flash : ''} ${className ?? ''}`}
      style={{ ...place(x, y, width), '--k': k, ...style } as CSSProperties}
    >
      {children}
    </button>
  )
}
