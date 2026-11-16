/**
 * 客廳＝「今日」（/today，DESIGN.md 5.1）。舊前端沒有這一頁，資料來自舊的紀錄頁與規劃頁用的同一批端點：
 *   GET /meal-plans?date=今天      茶几：今天各餐別已採用的餐食（快照，只能看）
 *   GET /meal-plans/trend          電視：近 7 天每一筆已採用餐食的熱量與目標
 *   GET /meal-requests、GET /jobs  廚房門口的鍋：最新一份還沒採用的問卷煮到哪了
 *
 * 可以操作的東西（手機的物件列也是這三樣）：
 * - 全家福 → /family
 * - 電視 → 切換早／午／晚（記在網址的 ?meal=，重新整理或分享連結都留得住）
 * - 廚房門口 → /plan
 * 茶几上的盤子與電視櫃前的小卡是擺飾，不能點。
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { apiError, errorMessage, errorStatus } from '../../api/client'
import { MEAL_LABELS, type MealType } from '../../api/types'
import z0 from '../../assets/rooms/living/z0.svg'
import z1 from '../../assets/rooms/living/z1.svg'
import z2 from '../../assets/rooms/living/z2.svg'
import z3 from '../../assets/rooms/living/z3.svg'
import { Hotspot, Placed, Room, type HotspotItem } from '../../camera/Room'
import type { Stop } from '../../camera/framing'
import { Button } from '../../components/Button'
import { HudNote } from '../../components/HudNote'
import { Notice } from '../../components/Notice'
import { Pot } from '../../pot/Pot'
import { potLabel, stovePots, type PotState } from '../../pot/potState'
import { localDate, mealRequestsQuery } from '../../session/queries'
import { SceneHud } from '../../shell/SceneHud'
import { TodayTable, type TodayMeal } from './TodayCard'
import { Tv, type TvTrend } from './Tv'
import { JOB_POLL_MS, generationJobsQuery, isActiveJob, plansOnQuery, trendQuery } from './queries'
import { dayBars, defaultMeal, nextMeal, parseMeal, planForMeal, trendItems } from './today'
import styles from './LivingScene.module.css'

const LAYERS = [z0, z1, z2, z3]
// 長螢幕手機一次只看得到約 430 個畫稿單位寬。電視牆那一屏由上而下是全家福、電視、小卡、茶几；
// 廚房門口比 DESIGN 4.4 的 0.824 再往右一點，整個門洞（1196–1504）才不會貼著畫面右緣。
const STOPS: Stop[] = [
  { id: 'sofa', label: '沙發與窗', fx: 0.2 },
  { id: 'tv', label: '電視牆', fx: 0.5 },
  { id: 'kitchen', label: '廚房門口', fx: 0.84 },
]
// 畫稿座標：牆上的畫框 690–910 × 108–294；廚房門洞 1196–1504 × 106–664；門洞裡瓦斯爐的兩個爐口
const FRAME = { x: 800, y: 108, width: 220 }
const DOORWAY = { x: 1350, y: 106, width: 308 }
const BURNER_X = [1328, 1406]
const POT_Y = 388
const POT_W = 84

/** 登入過期（401）或還沒選家庭（409）：不是「再試一次」能解決的，要回大門或玄關。 */
function isSessionError(error: unknown): boolean {
  return errorStatus(error) === 401 || apiError(error)?.code === 'HOUSEHOLD_NOT_SELECTED'
}

export default function LivingScene() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  // 進客廳那一刻的時間：決定「今天」是哪一天、沒指定時先看哪一餐（開著過午夜要重新進來才會換日）
  const [enteredAt] = useState(() => new Date())
  const today = localDate(enteredAt)
  const meal = parseMeal(params.get('meal')) ?? defaultMeal(enteredAt.getHours())
  const mealLabel = MEAL_LABELS[meal]
  const setMeal = (next: MealType) => {
    // 換台不留歷史紀錄：按「上一頁」是離開客廳，不是一台一台倒回去
    setParams((previous) => {
      const updated = new URLSearchParams(previous)
      updated.set('meal', next)
      return updated
    }, { replace: true })
  }

  // 和廚房的單軌同一份清單（同一個 query key，共用快取）
  const requests = useQuery(mealRequestsQuery)
  const jobs = useQuery({
    ...generationJobsQuery,
    // 有工作在排隊或執行時持續追蹤，鍋才會自己從冒煙變成開蓋
    refetchInterval: (query) => (query.state.data?.some(isActiveJob) ? JOB_POLL_MS : false),
  })
  const trend = useQuery(trendQuery(today))
  const plans = useQuery(plansOnQuery(today))

  // 工作由進行中變成結束時，問卷的狀態也變了，重新讀清單
  const cooking = jobs.data?.some(isActiveJob) ?? false
  const wasCooking = useRef(false)
  useEffect(() => {
    if (wasCooking.current && !cooking) void queryClient.invalidateQueries({ queryKey: mealRequestsQuery.queryKey })
    wasCooking.current = cooking
  }, [cooking, queryClient])

  // 重新確認登入狀態；App.tsx 的路由守衛會把人帶回大門或玄關
  const sessionLost = [requests.error, jobs.error, trend.error, plans.error].some(isSessionError)
  useEffect(() => {
    if (sessionLost) void queryClient.invalidateQueries({ queryKey: ['me'] })
  }, [sessionLost, queryClient])

  // 讀不到問卷清單時不猜：爐上不放鍋，物件列也不寫狀態。工作清單讀不到時只看問卷自己的狀態
  const stove = requests.data ? stovePots(requests.data, jobs.data ?? []) : null
  const pots: (PotState | null)[] = stove ? [stove.left.state, stove.right?.state ?? null] : []
  const kitchenStatus = stove && stove.left.state.kind !== 'empty' ? potLabel(stove.left.state) : '規劃'

  // 背景重新讀取失敗時，手上還有上一次的資料就繼續顯示（錯誤另外在上方說明）
  const tvTrend: TvTrend = trend.data !== undefined
    ? { status: 'ready', bars: dayBars(trendItems(trend.data), meal, today) }
    : { status: trend.isError ? 'error' : 'loading' }
  const todayMeal: TodayMeal = plans.data !== undefined
    ? { status: 'ready', ...planForMeal(plans.data, meal) }
    : { status: plans.isError ? 'error' : 'loading' }

  const failed = [
    { name: '今天的餐單', query: plans },
    { name: '電視上的紀錄', query: trend },
    { name: '廚房的進度', query: requests },
  ].filter(({ query }) => query.isError)
  const retrying = failed.some(({ query }) => query.isFetching)
  const retry = () => failed.forEach(({ query }) => void query.refetch())

  const hotspots: HotspotItem[] = [
    { id: 'portrait', label: '全家福', status: '家庭', stop: 'tv', onActivate: () => navigate('/family') },
    { id: 'tv', label: '電視', status: mealLabel, stop: 'tv', onActivate: () => setMeal(nextMeal(meal)) },
    { id: 'kitchen', label: '廚房門口', status: kitchenStatus, stop: 'kitchen', onActivate: () => navigate('/plan') },
  ]

  return (
    <Room
      name="客廳" layers={LAYERS} stops={STOPS} initialStop="tv" hotspots={hotspots}
      hud={(
        <SceneHud tab="today">
          {failed.length > 0 && !sessionLost && (
            <HudNote>
              <Notice
                tone="error" title={`讀不到${failed.map(({ name }) => name).join('、')}`}
                action={<Button busy={retrying} onClick={retry}>{retrying ? '重試中…' : '再試一次'}</Button>}
              >
                {errorMessage(failed[0].query.error)}
              </Notice>
            </HudNote>
          )}
        </SceneHud>
      )}
    >
      {/* 廚房門口裡，瓦斯爐上的鍋（最多兩口） */}
      {pots.map((state, i) => state && (
        <Placed key={BURNER_X[i]} x={BURNER_X[i]} y={POT_Y} width={POT_W} k={0.15}>
          <Pot view="side" state={state} />
        </Placed>
      ))}

      <Hotspot
        id="portrait" label="看全家福，管理家庭成員" {...FRAME} k={0.15}
        className={`${styles.spot} ${styles.frame}`} onClick={() => navigate('/family')}
      >
        <span className={styles.tag}>全家福・家庭</span>
      </Hotspot>

      <Tv meal={meal} onMeal={setMeal} trend={tvTrend} />
      <TodayTable meal={meal} today={today} state={todayMeal} />

      <Hotspot
        id="kitchen" label={`到廚房規劃餐食（${kitchenStatus}）`} {...DOORWAY} k={0.15}
        className={`${styles.spot} ${styles.doorway}`} onClick={() => navigate('/plan')}
      >
        <span className={styles.tag}>廚房・{kitchenStatus}</span>
      </Hotspot>
    </Room>
  )
}
