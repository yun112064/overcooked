/**
 * 冰箱門＝紀錄（/records?tab=adopted｜nutrition&date=YYYY-MM-DD；DESIGN.md 5.7）。
 * 左半「已採用」：月曆（有貼紙的日子有採用的餐單）＋當日餐單；右半「營養與採購」：成本／熱量趨勢＋生成紀錄。
 * 桌機兩半同時看得到；手機一半是一個停靠點，門上的兩顆大磁鐵就是分頁。
 * 把手是捷徑：開一張今天的新單，直接到「家裡有的食材」那一步拍冰箱。
 *
 * 端點、query key 與規則都和舊前端的「已採用紀錄」「營養與採購紀錄」兩頁相同；
 * 顯示的都是採用當下的快照，這裡只能看，不能改。
 *
 * 檔案分工：layout.ts 位置、停靠點與讓位的算法；records.ts 日期、趨勢與餐單的判斷；DoorNotes.tsx 門上的四張便利貼；
 * PlanDetail.tsx 一份餐單的完整明細（面板）；useMarketCheckout.ts 一鍵購入。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { api, apiError, errorMessage, errorStatus } from '../../api/client'
import type { MealRequest } from '../../api/types'
import { MEAL_LABELS } from '../../api/types'
import z0 from '../../assets/rooms/fridge/z0.svg'
import z1 from '../../assets/rooms/fridge/z1.svg'
import z2 from '../../assets/rooms/fridge/z2.svg'
import z3 from '../../assets/rooms/fridge/z3.svg'
import { Hotspot, Placed, Room, useRoom, type HotspotItem } from '../../camera/Room'
import { Button } from '../../components/Button'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { Panel } from '../../components/Panel'
import {
  localDate, mealRequestQuery, mealRequestsQuery, planDatesQuery, plansOnDateQuery, trendQuery,
} from '../../session/queries'
import { SceneHud } from '../../shell/SceneHud'
import { CalendarNote, DayNote, MealRows, RequestRows, RequestsNote, TrendNote } from './DoorNotes'
import { COLUMN_Y, HANDLE, LEFT, RIGHT, STOPS, TAB_Y, doorFit } from './layout'
import { PlanDetail } from './PlanDetail'
import {
  addDays, canCheckout, dayEntry, dayLabel, longDayLabel, marksMayBeIncomplete, normalizeTrend, noteElementId,
  planTitle, shownMonth, tablePath, validDay, weekdayLabel,
  type AdoptedPlan, type Tab, type TrendDays, type TrendItem, type TrendKind,
} from './records'
import { useMarketCheckout } from './useMarketCheckout'
import styles from './FridgeScene.module.css'

const LAYERS = [z0, z1, z2, z3]

/** 右邊便利貼上最多列幾張單，其餘在「全部」面板 */
const REQUESTS_ON_NOTE = 3

/** plan＝一份餐單的明細；day＝當天好幾餐的清單（從物件列進來）；requests＝全部的生成紀錄 */
type OpenPanel = { kind: 'plan'; id: string } | { kind: 'day' } | { kind: 'requests' } | null

function focusNote(id: string) {
  document.getElementById(noteElementId(id))?.focus()
}

/**
 * 門上的磁鐵與便利貼欄：位置照畫稿，另外依現在的取景往下讓位、算出欄最多可以多高（layout.ts 的 doorFit）。
 * 要問房間現在的取景，所以得是 <Room> 裡面的元件；數字交給樣式（FridgeScene.module.css 的 .onDoor、.column）。
 */
function OnDoor({ x, y, width, children }: { x: number; y: number; width?: number; children: ReactNode }) {
  const { view, frame, phone } = useRoom()
  const fit = doorFit(view, frame, phone)
  const style = { '--door-push': `${fit.push}px`, '--column-room': `${fit.room}px` } as CSSProperties
  return <Placed x={x} y={y} width={width} k={0.4} className={styles.onDoor} style={style}>{children}</Placed>
}

export default function FridgeScene() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const today = localDate()
  const tab: Tab = params.get('tab') === 'nutrition' ? 'nutrition' : 'adopted'
  const date = validDay(params.get('date')) ?? today

  const [turned, setTurned] = useState<{ forDate: string; month: string } | null>(null)
  // 冰箱門上的圖表窄，預設看一週（設計稿的每週長條）；30／90 天和舊頁面一樣可以切
  const [days, setDays] = useState<TrendDays>(7)
  const [kind, setKind] = useState<TrendKind>('cost')
  const [panel, setPanel] = useState<OpenPanel>(null)
  const [asking, setAsking] = useState(false)
  const month = shownMonth(date, turned)

  const setTab = (next: Tab) => {
    setParams((prev) => {
      const p = new URLSearchParams(prev)
      p.set('tab', next)
      return p
    }, { replace: true })
  }
  // 選一天：月曆與當日餐單在「已採用」那一半，所以手機上鏡頭也跟著轉過去
  const pick = (next: string) => {
    setTurned(null)
    // 明細和當日清單都是那一天的東西，換了日期就收起來
    setPanel((current) => (current?.kind === 'requests' ? current : null))
    setParams((prev) => {
      const p = new URLSearchParams(prev)
      p.set('tab', 'adopted')
      p.set('date', next)
      return p
    })
  }

  // 這三個查詢和客廳、廚房便利貼共用快取（session/queries.ts）
  const dates = useQuery(planDatesQuery)
  const plans = useQuery(plansOnDateQuery<AdoptedPlan>(date))
  const trend = useQuery({
    // 截止日用本機今天：預先安排的未來餐食不列入統計，與圖表範圍一致
    ...trendQuery<TrendItem>(days, today),
    select: (raw) => normalizeTrend(raw, days, today),
  })
  const requests = useQuery(mealRequestsQuery)
  const checkout = useMarketCheckout()

  // 登入過期或還沒選家庭（例如伺服器重啟）：讓進門的檢查重新判斷，回大門或玄關
  const failure = [dates.error, plans.error, trend.error, requests.error].find(Boolean)
  const signedOut = errorStatus(failure) === 401
  const noHousehold = apiError(failure)?.code === 'HOUSEHOLD_NOT_SELECTED'
  useEffect(() => {
    if (!signedOut && !noHousehold) return
    void queryClient.invalidateQueries({ queryKey: ['me'] })
    if (noHousehold) navigate('/households', { replace: true })
  }, [signedOut, noHousehold, queryClient, navigate])

  // 把手：和舊的「新增問卷」同一個請求（今天的日期），成功後直接到第 3 步拍食材
  const create = useMutation({
    mutationFn: async () => (await api.post<MealRequest>('/meal-requests', { meal_date: today })).data,
    onSuccess: (row) => {
      // 問卷那一景用同一個 key 讀這份資料，先放進快取就不用再等一次
      queryClient.setQueryData(mealRequestQuery(row.id).queryKey, row)
      void queryClient.invalidateQueries({ queryKey: mealRequestsQuery.queryKey })
      navigate(`/plan/${row.id}?step=3`)
    },
  })

  const dateList = dates.data ?? []
  const counts = new Map(dateList.map((d) => [d.meal_date, d.count]))
  const latestDate = dateList.reduce<string | null>((max, d) => (max === null || d.meal_date > max ? d.meal_date : max), null)
  const dayPlans = plans.data ?? []
  const buyable = dayPlans.filter(canCheckout)
  const openPlan = panel?.kind === 'plan' ? dayPlans.find((p) => p.id === panel.id) : undefined
  const openPlanTable = openPlan ? tablePath(openPlan) : null
  const requestList = requests.data ?? []

  const hotspots: HotspotItem[] = [
    { id: 'calendar', label: '月曆', status: dayLabel(date), stop: 'adopted', onActivate: () => focusNote('calendar') },
    {
      id: 'day', label: '當日餐單', stop: 'adopted',
      status: plans.data ? (dayPlans.length > 0 ? `${dayPlans.length} 餐` : '沒有紀錄') : undefined,
      onActivate: () => {
        const entry = dayEntry(dayPlans)
        if (entry.kind === 'note') focusNote('day')
        else setPanel(entry)
      },
    },
    ...buyable.map((plan) => ({
      id: `buy-${plan.id}`, label: '一鍵購入', status: MEAL_LABELS[plan.meal_type] ?? plan.meal_type, stop: 'adopted',
      onActivate: () => checkout.open(plan.id),
    })),
    { id: 'handle', label: '拉開冰箱', status: '拍食材', stop: 'adopted', onActivate: () => setAsking(true) },
    {
      id: 'trend', label: kind === 'cost' ? '成本趨勢' : '熱量趨勢', status: `近 ${days} 天`, stop: 'nutrition',
      onActivate: () => focusNote('trend'),
    },
    {
      id: 'requests', label: '生成紀錄', stop: 'nutrition',
      status: requests.data ? `${requestList.length} 筆` : undefined,
      onActivate: () => (requestList.length > REQUESTS_ON_NOTE ? setPanel({ kind: 'requests' }) : focusNote('requests')),
    },
  ]

  return (
    <Room
      name="冰箱門" layers={LAYERS} stops={STOPS} stopId={tab} onStopChange={(id) => setTab(id === 'nutrition' ? 'nutrition' : 'adopted')}
      hotspots={hotspots}
      // 手機上側邊的換邊按鈕預設在畫面 42% 高，會蓋到月曆最右邊那一格；改到磁鐵那一排（另一顆磁鐵本來就該在那個方向）
      edgeTop="66px"
      hud={(
        <SceneHud tab="records">
          {openPlan && (
            <Panel
              title={`${planTitle(openPlan)}・已採用`} side="right" onClose={() => setPanel(null)}
              footer={(openPlanTable || canCheckout(openPlan)) && (
                <>
                  {openPlanTable && <Link className={styles.link} to={openPlanTable}>到餐桌看這一桌（唯讀）</Link>}
                  {canCheckout(openPlan) && (
                    <Button variant="primary" busy={checkout.pendingId === openPlan.id} disabled={checkout.pendingId !== null} onClick={() => checkout.open(openPlan.id)}>
                      {checkout.pendingId === openPlan.id ? '開啟超市中…' : '一鍵購入'}
                    </Button>
                  )}
                </>
              )}
            >
              <PlanDetail plan={openPlan} />
              {checkout.errorId === openPlan.id && <p className={styles.panelError} role="alert">{checkout.errorText}</p>}
            </Panel>
          )}
          {/* 物件列的「當日餐單」：一天有好幾餐時在這裡選。畫面矮的時候便利貼要捲才看得到，這裡一定點得到每一餐 */}
          {panel?.kind === 'day' && (
            <Panel title={`${dayLabel(date)}（${weekdayLabel(date)}）採用的餐單`} side="right" onClose={() => setPanel(null)}>
              {dayPlans.length > 0
                ? <MealRows plans={dayPlans} checkout={checkout} onOpen={(id) => setPanel({ kind: 'plan', id })} />
                : <p className={styles.panelEmpty}>{longDayLabel(date)}沒有已採用的餐食紀錄。</p>}
            </Panel>
          )}
          {panel?.kind === 'requests' && (
            <Panel title={`生成紀錄（${requestList.length} 筆）`} side="left" onClose={() => setPanel(null)}>
              <RequestRows requests={requestList} />
            </Panel>
          )}
          {asking && (
            <ConfirmDialog
              title="拉開冰箱，開新的一餐？" confirmLabel={create.isPending ? '準備中…' : '開始拍食材'}
              busy={create.isPending} error={create.isError ? errorMessage(create.error) : undefined}
              onConfirm={() => create.mutate()}
              onCancel={() => { setAsking(false); create.reset() }}
            >
              會先開一張今天的新單，再帶你到「家裡有的食材」那一步，用相機拍冰箱裡有什麼。
            </ConfirmDialog>
          )}
        </SceneHud>
      )}
    >
      <Hotspot
        id="handle" label="拉開冰箱：開新的一餐，先拍家裡有的食材" x={HANDLE.x} y={HANDLE.y} width={HANDLE.width} k={0.4}
        className={styles.handle} onClick={() => setAsking(true)}
      >
        <span className={styles.tag}>拉開冰箱</span>
      </Hotspot>

      <OnDoor x={LEFT.x} y={TAB_Y}>
        <button type="button" className={styles.tab} aria-pressed={tab === 'adopted'} onClick={() => setTab('adopted')}>
          <i aria-hidden="true" />已採用
        </button>
      </OnDoor>
      <OnDoor x={RIGHT.x} y={TAB_Y}>
        <button type="button" className={`${styles.tab} ${styles.tabGreen}`} aria-pressed={tab === 'nutrition'} onClick={() => setTab('nutrition')}>
          <i aria-hidden="true" />營養與採購
        </button>
      </OnDoor>

      <OnDoor x={LEFT.x} y={COLUMN_Y} width={LEFT.width}>
        <div className={styles.column}>
          <CalendarNote
            month={month} selected={date} today={today} counts={counts} dates={dates}
            incomplete={marksMayBeIncomplete(dateList, month)}
            onMonth={(next) => setTurned({ forDate: date, month: next })} onPick={pick}
          />
          <DayNote
            date={date} plans={plans} latestDate={latestDate} neverAdopted={dates.data !== undefined && dateList.length === 0}
            checkout={checkout} onOpen={(id) => setPanel({ kind: 'plan', id })} onPick={pick}
            onStep={(delta) => pick(addDays(date, delta))}
          />
        </div>
      </OnDoor>

      <OnDoor x={RIGHT.x} y={COLUMN_Y} width={RIGHT.width}>
        <div className={styles.column}>
          <TrendNote days={days} kind={kind} trend={trend} selected={date} onDays={setDays} onKind={setKind} onPick={pick} />
          <RequestsNote requests={requests} limit={REQUESTS_ON_NOTE} onMore={() => setPanel({ kind: 'requests' })} />
        </div>
      </OnDoor>
    </Room>
  )
}
