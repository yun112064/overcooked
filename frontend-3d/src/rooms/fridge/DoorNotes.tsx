/**
 * 貼在冰箱門上的四張便利貼：月曆、當日餐單（左半「已採用」），趨勢、生成紀錄（右半「營養與採購」）。
 * 便利貼只放摘要與入口；整份餐單的明細在 PlanDetail（面板）裡。
 * 未知的值一律寫「未知」並用虛線樣式，不畫成有高度的長條。
 */
import { useId, type ReactNode } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../../api/client'
import type { MealRequestSummary } from '../../api/types'
import { MEAL_LABELS } from '../../api/types'
import { useRoom } from '../../camera/Room'
import { Money } from '../../components/Money'
import { NutritionBadge } from '../../components/NutritionBadge'
import { Spinner } from '../../components/Spinner'
import { StatusChip } from '../../components/StatusChip'
import { UnknownValue } from '../../components/UnknownValue'
import { requestStatusInfo } from '../../components/requestStatus'
import {
  TREND_DAYS, axisLabels, barView, byDay, canCheckout, dayLabel, describePoint, hasPartialBars, longDayLabel, monthCells,
  monthTitle, noteElementId, planDishNames, planEnergy, requestNote, requestPath, requestTitle, scaleTop, shiftMonth,
  tablePath, trendStats, validDay, weekdayLabel,
  type AdoptedPlan, type DayPoint, type Trend, type TrendDays, type TrendKind,
} from './records'
import type { MarketCheckout } from './useMarketCheckout'
import styles from './DoorNotes.module.css'

/** useQuery 回傳值裡這裡用得到的部分（方便各張便利貼只拿自己要的） */
type Loaded<T> = {
  data: T | undefined
  isPending: boolean
  isError: boolean
  isFetching: boolean
  error: unknown
  refetch: () => unknown
}

type NoteProps = {
  /** 和物件列的 id 相同；被物件列點到時會閃一下，也是鍵盤跳過來的落點 */
  id: string
  tone: 'white' | 'yellow' | 'green'
  magnet: 'red' | 'green' | 'blue'
  title: ReactNode
  aside?: ReactNode
  /** 桌機上欄太高時由這張便利貼讓出高度（裡面的清單改成捲動）；月曆和圖表不縮。手機是整欄一起捲 */
  shrink?: boolean
  children: ReactNode
}

/** 一張用磁鐵吸在門上的便利貼。 */
function Note({ id, tone, magnet, title, aside, shrink = false, children }: NoteProps) {
  const { flashId } = useRoom()
  const titleId = useId()
  return (
    <section
      id={noteElementId(id)} tabIndex={-1} aria-labelledby={titleId}
      className={`${styles.note} ${styles[tone]} ${shrink ? styles.shrink : ''} ${flashId === id ? styles.flash : ''}`}
    >
      <i className={`${styles.magnet} ${styles[`magnet-${magnet}`]}`} aria-hidden="true" />
      <header className={styles.head}>
        <h3 id={titleId}>{title}</h3>
        {aside}
      </header>
      {children}
    </section>
  )
}

/** 便利貼裡的讀取失敗：一定有文字，並可以重試。 */
function LoadError({ error, onRetry, retrying }: { error: unknown; onRetry: () => unknown; retrying: boolean }) {
  return (
    <p className={styles.error} role="alert">
      <span>{errorMessage(error)}</span>
      <button type="button" className={styles.small} disabled={retrying} onClick={() => { onRetry() }}>
        {retrying ? '重試中…' : '再試一次'}
      </button>
    </p>
  )
}

// ---- 月曆 ----

type CalendarNoteProps = {
  month: string
  selected: string
  today: string
  /** 有採用餐單的日子 → 餐數 */
  counts: Map<string, number>
  dates: Loaded<unknown>
  /** 看的月份比日期清單涵蓋的更早：沒有貼紙不代表沒有紀錄 */
  incomplete: boolean
  onMonth: (month: string) => void
  onPick: (date: string) => void
}

const WEEK = ['日', '一', '二', '三', '四', '五', '六']
const MAX_STICKERS = 3

export function CalendarNote({ month, selected, today, counts, dates, incomplete, onMonth, onPick }: CalendarNoteProps) {
  const cells = monthCells(month)
  return (
    <Note
      id="calendar" tone="white" magnet="red" title={monthTitle(month)}
      aside={(
        <span className={styles.monthNav}>
          <button type="button" className={styles.icon} aria-label="上個月" onClick={() => onMonth(shiftMonth(month, -1))}>‹</button>
          <button type="button" className={styles.icon} aria-label="下個月" onClick={() => onMonth(shiftMonth(month, 1))}>›</button>
        </span>
      )}
    >
      <div className={styles.calendar} role="group" aria-label={`${monthTitle(month)}的日期`}>
        {WEEK.map((w) => <u key={w} aria-hidden="true">{w}</u>)}
        {cells.map((date, i) => {
          if (!date) return <span key={`blank-${i}`} />
          const count = counts.get(date) ?? 0
          const day = Number(date.slice(8))
          return (
            <button
              key={date} type="button" className={styles.day}
              aria-pressed={date === selected} aria-current={date === today ? 'date' : undefined}
              aria-label={`${longDayLabel(date)}${date === today ? '，今天' : ''}，${count > 0 ? `${count} 餐已採用` : '沒有標記'}`}
              onClick={() => onPick(date)}
            >
              <b>{day}</b>
              {/* 餐盤貼紙：一餐一張，超過三張只畫三張（餐數寫在名稱裡） */}
              <span className={styles.stickers} aria-hidden="true">
                {Array.from({ length: Math.min(count, MAX_STICKERS) }, (_, k) => <i key={k} />)}
              </span>
            </button>
          )
        })}
      </div>
      <div className={styles.calendarFoot}>
        <button type="button" className={styles.small} disabled={selected === today} onClick={() => onPick(today)}>今天</button>
        <label className={styles.jump}>
          <span>跳到</span>
          <input
            type="date" value={selected} aria-label="跳到指定日期"
            onChange={(event) => { const next = validDay(event.target.value); if (next) onPick(next) }}
          />
        </label>
      </div>
      {dates.isError && <LoadError error={dates.error} onRetry={dates.refetch} retrying={dates.isFetching} />}
      <p className={styles.hint}>
        <i className={styles.legendSticker} aria-hidden="true" />有貼紙的日子＝那天有採用的餐單。只記錄按「採用」保存的餐食，不代表實際吃了。
        {incomplete && ' 較早的月份貼紙可能不完整，點日期一樣查得到。'}
      </p>
    </Note>
  )
}

// ---- 當日餐單 ----

type DayNoteProps = {
  date: string
  plans: Loaded<AdoptedPlan[]>
  /** 最近一個有紀錄的日子（空的那天用來帶路）；完全沒有紀錄為 null */
  latestDate: string | null
  /** 這個家庭從來沒有採用過（日期清單讀到了而且是空的） */
  neverAdopted: boolean
  checkout: MarketCheckout
  onOpen: (planId: string) => void
  onPick: (date: string) => void
  /** 前一天（-1）／後一天（+1） */
  onStep: (delta: -1 | 1) => void
}

export function DayNote({ date, plans, latestDate, neverAdopted, checkout, onOpen, onPick, onStep }: DayNoteProps) {
  const rows = plans.data ?? []
  return (
    <Note
      id="day" tone="yellow" magnet="green" shrink title={`${dayLabel(date)}（${weekdayLabel(date)}）採用的餐單`}
      // 一天一天翻：不用回月曆找格子（手機上月曆的邊緣可能被換邊的按鈕擋到）
      aside={(
        <span className={styles.monthNav}>
          <button type="button" className={styles.icon} aria-label="前一天" onClick={() => onStep(-1)}>‹</button>
          <button type="button" className={styles.icon} aria-label="後一天" onClick={() => onStep(1)}>›</button>
        </span>
      )}
    >
      {plans.isPending && <Spinner label="正在翻那天的紀錄…" />}
      {plans.isError && <LoadError error={plans.error} onRetry={plans.refetch} retrying={plans.isFetching} />}
      {plans.data && rows.length === 0 && (
        <div className={styles.empty}>
          {neverAdopted ? (
            <>
              <p><b>還沒有採用過菜單。</b>產生菜單並按「採用」之後，這裡會記下每一餐，並畫出熱量與成本的趨勢。</p>
              <Link className={styles.small} to="/plan">去廚房規劃一餐</Link>
            </>
          ) : (
            <>
              <p>{longDayLabel(date)}沒有已採用的餐食紀錄。</p>
              {latestDate && latestDate !== date && (
                <button type="button" className={styles.small} onClick={() => onPick(latestDate)}>
                  看最近一筆（{dayLabel(latestDate)}）
                </button>
              )}
            </>
          )}
        </div>
      )}
      {rows.length > 0 && <MealRows plans={rows} checkout={checkout} onOpen={onOpen} />}
    </Note>
  )
}

/** 一列一餐：菜名與整餐熱量（點了開明細）、回餐桌的連結、一鍵購入。便利貼與物件列開的「當日餐單」面板共用。 */
export function MealRows({ plans, checkout, onOpen }: {
  plans: AdoptedPlan[]; checkout: MarketCheckout; onOpen: (planId: string) => void
}) {
  return (
    <ul className={styles.meals}>
      {plans.map((plan) => {
        const energy = planEnergy(plan)
        const meal = MEAL_LABELS[plan.meal_type] ?? plan.meal_type
        const table = tablePath(plan)
        return (
          <li key={plan.id}>
            <button type="button" className={styles.meal} onClick={() => onOpen(plan.id)}>
              <b>{meal}</b>
              <span className={styles.dishes}>{planDishNames(plan).join('、') || '沒有菜名紀錄'}</span>
              <small>
                整餐熱量 {energy === null ? <UnknownValue /> : `${Math.round(energy).toLocaleString('en-US')} kcal`}・看明細 ›
              </small>
            </button>
            {/* 手機上一列放不下，這個連結收在明細面板的底部（見樣式） */}
            {table && (
              <Link className={`${styles.small} ${styles.tableLink}`} to={table} draggable={false} aria-label={`${meal}：到餐桌看這一桌（唯讀）`}>
                看這桌
              </Link>
            )}
            {canCheckout(plan) && (
              <button
                type="button" className={styles.buy} disabled={checkout.pendingId !== null}
                aria-label={`${meal}：一鍵購入缺的食材`} onClick={() => checkout.open(plan.id)}
              >
                {checkout.pendingId === plan.id ? '開啟超市中…' : '一鍵購入'}
              </button>
            )}
            {checkout.errorId === plan.id && <p className={styles.error} role="alert">{checkout.errorText}</p>}
          </li>
        )
      })}
    </ul>
  )
}

// ---- 趨勢（成本／熱量） ----

type TrendNoteProps = {
  days: TrendDays
  kind: TrendKind
  trend: Loaded<Trend>
  /** 目前選的日期（和月曆同一個），長條上會標出來 */
  selected: string
  onDays: (days: TrendDays) => void
  onKind: (kind: TrendKind) => void
  onPick: (date: string) => void
}

const KINDS: { id: TrendKind; label: string }[] = [{ id: 'cost', label: '成本' }, { id: 'energy', label: '熱量' }]

export function TrendNote({ days, kind, trend, selected, onDays, onKind, onPick }: TrendNoteProps) {
  const data = trend.data
  const stats = data ? trendStats(data.items) : null
  const points = data ? byDay(data.items, data.since, days) : []
  const tappable = barsTappable(points)
  return (
    <Note id="trend" tone="white" magnet="green" title={kind === 'cost' ? '每日按比例成本' : '每日熱量'}>
      <div className={styles.switches}>
        <span className={styles.seg} role="group" aria-label="看多久">
          {TREND_DAYS.map((d) => (
            <button key={d} type="button" aria-pressed={d === days} onClick={() => onDays(d)}>{d} 天</button>
          ))}
        </span>
        <span className={styles.seg} role="group" aria-label="看什麼">
          {KINDS.map((k) => (
            <button key={k.id} type="button" aria-pressed={k.id === kind} onClick={() => onKind(k.id)}>{k.label}</button>
          ))}
        </span>
      </div>
      {trend.isPending && <Spinner label="正在整理趨勢…" />}
      {trend.isError && <LoadError error={trend.error} onRetry={trend.refetch} retrying={trend.isFetching} />}
      {data && stats && (stats.meals === 0 ? (
        <p className={styles.empty}>近 {days} 天沒有已採用的餐食，採用菜單後這裡會出現趨勢。</p>
      ) : (
        <>
          <Bars points={points} kind={kind} selected={selected} onPick={onPick} />
          {kind === 'cost' ? (
            <p className={styles.stats}>
              <span>近 {days} 天採用 <b>{stats.meals}</b> 餐</span>
              <span>平均每餐 <Money value={stats.averageCost} /></span>
              {stats.pricedMeals > 0 && <span>預算內 <b>{stats.underBudget}/{stats.pricedMeals}</b> 餐</span>}
              {stats.unknownCostMeals > 0 && <span className={styles.unknownNote}>{stats.unknownCostMeals} 餐價格未知</span>}
            </p>
          ) : (
            <p className={styles.stats}>
              <span>近 {days} 天採用 <b>{stats.meals}</b> 餐，平均每餐：</span>
              <NutritionBadge nutrient="energy_kcal" ratio={stats.energyReach} />
              <NutritionBadge nutrient="protein_g" ratio={stats.proteinReach} />
              {stats.energyReach === null && <span className={styles.unknownNote}>沒有可比較的紀錄</span>}
            </p>
          )}
          <p className={styles.hint}>
            {kind === 'cost'
              ? '按比例成本只算用掉的份量，和整包花費是兩個數字。橫線是預算。'
              : '橫線是當天的熱量目標；熱量不超過目標 110%、蛋白質達 90% 算通過。'}
            {/* 成本和熱量都可能只算到一部分；圖上有這種長條就要寫出來，不然會被當成整天的數字 */}
            {hasPartialBars(points, kind) && ' 虛線框的長條只計已知的部分，不是整天的數字。'}
            {tappable && ' 點長條看當天的餐單。'}
          </p>
        </>
      ))}
    </Note>
  )
}

/**
 * 長條本身可不可以點。只有看一週時一格才夠寬（手機約 48px）；
 * 30／90 天一格只剩幾 px，做成按鈕會點到隔壁那一天，鍵盤也要多走幾十站，所以改用圖下面的選單選日期。
 */
function barsTappable(points: DayPoint[]): boolean {
  return points.length <= 7
}

function Bars({ points, kind, selected, onPick }: {
  points: DayPoint[]; kind: TrendKind; selected: string; onPick: (date: string) => void
}) {
  const top = scaleTop(points, kind)
  const unit = kind === 'cost' ? '元' : 'kcal'
  const tappable = barsTappable(points)
  // 選單裡最近的日子排前面
  const recorded = points.filter((p) => p.meals > 0).reverse()
  return (
    <figure className={styles.chart}>
      <figcaption>
        <span>刻度上緣 {top.toLocaleString('en-US')} {unit}</span>
        <span className={styles.legend}>
          <i className={styles.legendBar} aria-hidden="true" />{kind === 'cost' ? '成本' : '實際'}
          <i className={styles.legendTick} aria-hidden="true" />{kind === 'cost' ? '預算' : '目標'}
          {hasPartialBars(points, kind) && <><i className={`${styles.legendPartial} ${styles[kind]}`} aria-hidden="true" />部分已知</>}
          <i className={styles.legendUnknown} aria-hidden="true" />未知
        </span>
      </figcaption>
      {/* 不能點的時候整張圖只是畫給眼睛看的；每一天的數字在下面的選單裡，讀屏從那裡讀 */}
      <div className={styles.bars} data-dense={points.length > 31 || undefined} aria-hidden={!tappable || undefined}>
        {points.map((p) => {
          if (p.meals === 0) return <span key={p.date} className={styles.slot} />
          const view = barView(p, kind, top)
          const text = describePoint(p, kind)
          const current = p.date === selected || undefined
          const marks = (
            <>
              {view.height === null
                // 未知：不畫長條，只在底線上放一個虛線的問號
                ? <i className={styles.barUnknown}>?</i>
                : <i className={`${styles.bar} ${styles[kind]} ${view.partial ? styles.partial : ''}`} style={{ height: `${view.height}%` }} />}
              {view.tick !== null && <i className={styles.tick} style={{ bottom: `${view.tick}%` }} />}
            </>
          )
          return tappable ? (
            <button
              key={p.date} type="button" className={styles.slot} title={text} data-current={current}
              aria-label={`${text}，看當天的餐單`} aria-pressed={p.date === selected} onClick={() => onPick(p.date)}
            >
              {marks}
            </button>
          ) : <span key={p.date} className={styles.slot} title={text} data-current={current}>{marks}</span>
        })}
      </div>
      <div className={styles.axis} aria-hidden="true">
        {axisLabels(points).map((label, i) => <span key={`${label}-${i}`}>{label}</span>)}
      </div>
      {!tappable && (
        <label className={styles.dayPick}>
          <span>看某一天的餐單</span>
          <select
            value={recorded.some((p) => p.date === selected) ? selected : ''}
            onChange={(event) => { if (event.target.value) onPick(event.target.value) }}
          >
            <option value="">選一天…</option>
            {recorded.map((p) => <option key={p.date} value={p.date}>{describePoint(p, kind)}</option>)}
          </select>
        </label>
      )}
    </figure>
  )
}

// ---- 生成紀錄（問卷清單） ----

/** 一列一張問卷：日期餐別、第幾版、狀態；整列是連結。便利貼與「全部」面板共用。 */
export function RequestRows({ requests }: { requests: MealRequestSummary[] }) {
  return (
    <ul className={styles.requests}>
      {requests.map((r) => {
        const status = requestStatusInfo(r.status)
        return (
          <li key={r.id}>
            <Link to={requestPath(r)} className={styles.request} draggable={false}>
              <span>
                <b>{requestTitle(r)}</b>
                <small>{requestNote(r)}</small>
              </span>
              <StatusChip tone={status.tone}>{status.label}</StatusChip>
            </Link>
          </li>
        )
      })}
    </ul>
  )
}

type RequestsNoteProps = {
  requests: Loaded<MealRequestSummary[]>
  /** 便利貼上最多列幾筆，其餘按「全部」看 */
  limit: number
  onMore: () => void
}

export function RequestsNote({ requests, limit, onMore }: RequestsNoteProps) {
  const rows = requests.data ?? []
  return (
    <Note
      id="requests" tone="green" magnet="red" shrink title="生成紀錄"
      aside={rows.length > limit && (
        <button type="button" className={styles.small} onClick={onMore}>全部 {rows.length} 筆</button>
      )}
    >
      {requests.isPending && <Spinner label="正在找紀錄…" />}
      {requests.isError && <LoadError error={requests.error} onRetry={requests.refetch} retrying={requests.isFetching} />}
      {requests.data && rows.length === 0 && (
        <div className={styles.empty}>
          <p>還沒有任何一張單。到廚房開一張，生成之後這裡會留下紀錄。</p>
          <Link className={styles.small} to="/plan">去廚房</Link>
        </div>
      )}
      {rows.length > 0 && <RequestRows requests={rows.slice(0, limit)} />}
      {rows.length > 0 && <p className={styles.hint}>點一列回到那張單：可以修改、生成菜單，或查看已採用的結果。</p>}
    </Note>
  )
}
