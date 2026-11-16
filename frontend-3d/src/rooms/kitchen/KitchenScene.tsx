/**
 * 廚房全景＝「規劃」（/plan，DESIGN.md 5.2）。舊前端「我的問卷」頁的功能都在這一景：
 *
 *   單軌      每張單＝一份問卷（日期、餐別、狀態貼紙），點了接續那一份；最後一個位置是「全部的單」面板
 *   瓦斯爐    左鍋＝最新一份還沒採用的問卷；右鍋＝備用空鍋（新的一餐）或第二口生成中的鍋
 *   食譜書    → 食譜庫（/pick）
 *   便利貼    最近採用、近 7 天 → 冰箱門（/records）
 *
 * 點單或鍋要去哪裡的規則在 kitchenLogic.ts 的 destinationFor()；鍋長什麼樣子由 src/pot 決定（和客廳門口同一套）。
 * 手機：三個停靠點，物件列列出單軌、食譜書、兩口鍋與兩張便利貼。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, apiError, errorStatus } from '../../api/client'
import type { MealRequest, MealRequestSummary } from '../../api/types'
import ticketArt from '../../assets/rooms/kitchen/ticket.svg'
import z0 from '../../assets/rooms/kitchen/z0.svg'
import z1 from '../../assets/rooms/kitchen/z1.svg'
import z2 from '../../assets/rooms/kitchen/z2.svg'
import z3 from '../../assets/rooms/kitchen/z3.svg'
import { Hotspot, Room, type HotspotItem } from '../../camera/Room'
import type { Stop } from '../../camera/framing'
import { Button } from '../../components/Button'
import { HudNote } from '../../components/HudNote'
import { ErrorNotice, Notice } from '../../components/Notice'
import { requestStatusInfo } from '../../components/requestStatus'
import { Spinner } from '../../components/Spinner'
import { StatusChip } from '../../components/StatusChip'
import { Pot } from '../../pot/Pot'
import { latestGenerationJob, potLabel, potState, stovePots, type PotState, type StovePot } from '../../pot/potState'
import { localDate, mealRequestQuery, mealRequestsQuery, membersQuery } from '../../session/queries'
import { useMe } from '../../session/session'
import { SceneHud } from '../../shell/SceneHud'
import { FridgeNotes } from './FridgeNotes'
import { NOTE_IDS, adoptedStatus, weekStatus } from './fridgeNoteText'
import {
  RAIL_TICKETS, anyCooking, destinationFor, potAction, railTickets, resultPath, ticketText,
} from './kitchenLogic'
import { generationJobsQuery, mealResultsQuery } from './kitchenQueries'
import { TicketListPanel } from './TicketListPanel'
import { useFridgeNotes } from './useFridgeNotes'
import styles from './KitchenScene.module.css'

const LAYERS = [z0, z1, z2, z3]
/*
 * 手機的三個停靠點。fx 是鏡頭中心（世界寬度的比例）、span 是一定要看到的寬度（世界單位＝畫稿單位 × 1.06）。
 * 長螢幕手機蓋滿畫面只看得到約 436 個畫稿單位，而且左右邊緣還壓著「‹ 上一段／下一段 ›」的按鈕（高度正好在單軌與鍋的位置），
 * 所以前兩個停靠點把鏡頭拉遠一點點，讓可點的東西不被邊緣按鈕蓋住：
 *   rail   單軌四個位置 156–548、食譜書 148–412；右邊留給「爐台 ›」
 *   stove  兩口鍋 686–974；左邊留給「‹ 出菜口與食譜書」、右邊留給「冰箱 ›」
 *   fridge 便利貼 1298–1516；鏡頭靠到世界右緣（fx 會被夾住）
 */
const STOPS: Stop[] = [
  { id: 'rail', label: '出菜口與食譜書', fx: 0.218, span: 488 },
  { id: 'stove', label: '爐台', fx: 0.504, span: 484 },
  { id: 'fridge', label: '冰箱', fx: 0.86, span: 340 },
]

// 畫稿座標。單軌 108–552 × 348–364：左邊讓開桌機的側欄，從 156 開始排四個位置
const SLOT_X = [202, 302, 402, 502]
const TICKET_Y = 352
const TICKET_W = 92
// 兩個爐口的中心在 752 與 908，爐架上緣約 552；鍋身（240×170）的底落在爐架上
const BURNER_X = { left: 752, right: 908 }
const POT_Y = 468
const POT_W = 131
// 闔上的食譜書 148–412 × 536–648
const BOOK = { x: 280, y: 536, width: 264 }

/** 生成中每隔多久重新讀一次（和舊前端的進度輪詢一樣是 2 秒） */
const POLL_MS = 2000

const EMPTY_POT: PotState = { kind: 'empty' }

type KitchenPot = StovePot<MealRequestSummary>
type BurnerPot = { id: string; side: 'left' | 'right'; name: string; pot: KitchenPot }

export default function KitchenScene() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [listOpen, setListOpen] = useState(false)
  const householdName = useMe().data?.current_household?.name

  // 兩份清單任何一邊看到「生成中」就一起輪詢：鍋會自己從冒煙變成開蓋，單上的貼紙也跟著換
  const pollInterval = () => (anyCooking(
    queryClient.getQueryData(mealRequestsQuery.queryKey), queryClient.getQueryData(generationJobsQuery.queryKey),
  ) ? POLL_MS : false)
  const list = useQuery({ ...mealRequestsQuery, refetchInterval: pollInterval })
  const jobs = useQuery({ ...generationJobsQuery, refetchInterval: pollInterval })
  // 面板打開才需要家人的名字
  const members = useQuery({ ...membersQuery, enabled: listOpen })
  const notes = useFridgeNotes()

  // 假後端重啟、或在別的分頁登出／換家庭：重新確認登入狀態，路由會把人帶回大門或玄關
  const listError = list.error
  useEffect(() => {
    const status = errorStatus(listError)
    if (status === 401 || (status === 409 && apiError(listError)?.code === 'HOUSEHOLD_NOT_SELECTED')) {
      void queryClient.invalidateQueries({ queryKey: ['me'] })
    }
  }, [listError, queryClient])

  // 和舊前端一樣：只帶今天的日期建立一份草稿，接著到問卷
  const create = useMutation({
    mutationFn: async () => (await api.post<MealRequest>('/meal-requests', { meal_date: localDate() })).data,
    onSuccess: (row) => {
      // 問卷那一景用同一個 key 讀這份資料，先放進快取就不用再等一次
      queryClient.setQueryData(mealRequestQuery(row.id).queryKey, row)
      void queryClient.invalidateQueries({ queryKey: mealRequestsQuery.queryKey })
      navigate(`/plan/${row.id}`)
    },
  })

  // 上桌前先查這份問卷最新的結果版本（重試查價或知識說明會產生新版本，所以不能用生成工作上記的版本）
  const openResult = useMutation({
    mutationFn: async (requestId: string) => ({
      requestId, results: await queryClient.fetchQuery({ ...mealResultsQuery(requestId), staleTime: 0 }),
    }),
    onSuccess: ({ requestId, results }) => navigate(resultPath(requestId, results)),
  })

  const busy = create.isPending || openResult.isPending
  const jobList = jobs.data ?? []
  const potOf = (request: MealRequestSummary) => potState({ request, job: latestGenerationJob(jobList, request.id) })

  const startNew = () => {
    if (!busy) create.mutate()
  }
  const openRequest = (request: MealRequestSummary) => {
    if (busy) return
    const destination = destinationFor(request, potOf(request))
    if (destination.kind === 'path') navigate(destination.to)
    else openResult.mutate(destination.requestId)
  }
  const openPot = (pot: KitchenPot) => (pot.request ? openRequest(pot.request) : startNew())
  const openAdopted = () => navigate(
    notes.adopted.status === 'ready' ? `/records?tab=adopted&date=${notes.adopted.date}` : '/records?tab=adopted',
  )
  const openWeek = () => navigate('/records?tab=nutrition')
  const retryResult = () => {
    if (openResult.variables !== undefined) openResult.mutate(openResult.variables)
  }

  const requests = list.data
  const rail = railTickets(requests ?? [])
  // 清單讀不到時不猜爐上有什麼：只放一口空鍋，點了照樣可以開新的一餐（舊前端的「新增問卷」也不受清單影響）
  const stove: { left: KitchenPot; right: KitchenPot | null } = requests
    ? stovePots(requests, jobList)
    : { left: { request: null, state: EMPTY_POT }, right: null }
  const pots: BurnerPot[] = [{ id: 'pot-left', side: 'left', name: '左鍋', pot: stove.left }]
  if (stove.right) pots.push({ id: 'pot-right', side: 'right', name: stove.right.request ? '右鍋' : '空鍋', pot: stove.right })
  // 空鍋但有對應的問卷＝剛開、還沒填的草稿：點了接著填那一份，不再多開一張
  const potTitle = (pot: KitchenPot) => (pot.state.kind === 'empty' ? (pot.request ? '還沒放料' : '空鍋') : potLabel(pot.state))

  const hotspots: HotspotItem[] = [
    {
      id: 'rail', label: '單軌', stop: 'rail', onActivate: () => setListOpen(true),
      status: requests ? (requests.length > 0 ? `${requests.length} 張單` : '還沒有單') : list.isError ? '讀不到' : undefined,
    },
    { id: 'book', label: '食譜書', status: '食譜庫', stop: 'rail', onActivate: () => navigate('/pick') },
    ...pots.map(({ id, name, pot }) => ({
      id, label: name, stop: 'stove', onActivate: () => openPot(pot),
      status: pot.request ? potTitle(pot) : '新的一餐',
    })),
    { id: NOTE_IDS.adopted, label: '便利貼・最近採用', status: adoptedStatus(notes.adopted), stop: 'fridge', onActivate: openAdopted },
    { id: NOTE_IDS.week, label: '便利貼・近 7 天', status: weekStatus(notes.week), stop: 'fridge', onActivate: openWeek },
  ]

  const errors = (
    <>
      {list.isError && <ErrorNotice error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />}
      {create.isError && <ErrorNotice error={create.error} onRetry={startNew} retryLabel="再開一次" />}
      {openResult.isError && (
        <ErrorNotice error={openResult.error} fallback="找不到這一餐的結果，請稍後再試。" onRetry={retryResult} />
      )}
    </>
  )

  return (
    <Room
      name="廚房" layers={LAYERS} stops={STOPS} initialStop="stove" hotspots={hotspots}
      hud={(
        <SceneHud tab="plan">
          <HudNote>
            {list.isPending && <Spinner label="正在看爐上有什麼…" />}
            {create.isPending && <Spinner label="正在開一張新的單…" />}
            {openResult.isPending && <Spinner label="正在把這一餐端上桌…" />}
            {/* 面板開著時，錯誤與空狀態都顯示在面板裡，免得同一句話出現兩次 */}
            {!listOpen && errors}
            {!listOpen && requests?.length === 0 && (
              <Notice
                title="規劃你家的第一餐"
                action={(
                  <span className={styles.noticeActions}>
                    <Button variant="primary" busy={create.isPending} disabled={busy} onClick={startNew}>新增問卷</Button>
                    <Button disabled={busy} onClick={() => navigate('/pick')}>從食譜庫選菜</Button>
                  </span>
                )}
              >
                建立一份問卷，告訴我們誰要吃、預算多少、家裡有什麼，就能得到一份營養剛好的菜單。也可以直接點爐上的空鍋。
              </Notice>
            )}
          </HudNote>
          {listOpen && (
            <TicketListPanel
              requests={requests} loading={list.isPending} errors={errors}
              householdName={householdName} memberNames={members.data?.map((member) => member.display_name)}
              busy={busy} creating={create.isPending}
              onOpen={openRequest} onCreate={startNew} onPick={() => navigate('/pick')} onClose={() => setListOpen(false)}
            />
          )}
        </SceneHud>
      )}
    >
      {/* 單軌：最新的三張單 */}
      {rail.shown.map((request, i) => {
        const text = ticketText(request)
        const status = requestStatusInfo(request.status)
        return (
          <Hotspot
            key={request.id} id={`ticket-${request.id}`} x={SLOT_X[i]} y={TICKET_Y} width={TICKET_W} k={0.15}
            label={`${text.date} ${text.weekday} ${text.meal}，${status.label}，${text.note}。打開這張單`}
            className={styles.ticket} style={{ backgroundImage: `url("${ticketArt}")` }}
            disabled={busy} onClick={() => openRequest(request)}
          >
            <span className={styles.ticketText}>
              <b>{text.date}</b>
              <span>{text.weekday}</span>
              <span>{text.meal}</span>
              <StatusChip tone={status.tone} className={styles.sticker}>{status.label}</StatusChip>
            </span>
          </Hotspot>
        )
      })}
      {/* 單軌的最後一個位置固定是「全部的單」（也是開新單、從食譜庫選菜的入口）；位置不跟著單的張數跑 */}
      <Hotspot
        id="rail" x={SLOT_X[RAIL_TICKETS]} y={TICKET_Y} width={TICKET_W} k={0.15}
        label={requests ? `看全部的單，共 ${requests.length} 張；也可以在這裡開新的單` : '看全部的單'}
        className={`${styles.ticket} ${styles.pad}`} style={{ backgroundImage: `url("${ticketArt}")` }}
        onClick={() => setListOpen(true)}
      >
        <span className={styles.ticketText}>
          <b>全部的單</b>
          {list.isPending && <span>讀取中…</span>}
          {list.isError && <span>讀不到</span>}
          {requests && <span>{requests.length > 0 ? `共 ${requests.length} 張` : '還沒有單'}</span>}
          {rail.hidden > 0 && <span>另有 {rail.hidden} 張</span>}
          <em>＋ 開新單</em>
        </span>
      </Hotspot>

      <Hotspot
        id="book" label="翻開食譜書，從食譜庫選菜" {...BOOK} k={0.7}
        className={styles.book} onClick={() => navigate('/pick')}
      >
        <span className={`${styles.tag} ${styles.tagNear}`}><b>食譜書</b><small>食譜庫選菜</small></span>
      </Hotspot>

      {pots.map(({ id, side, name, pot }) => {
        const title = potTitle(pot)
        const action = potAction(pot.state, pot.request !== null)
        return (
          <Hotspot
            key={id} id={id}
            label={pot.request ? `${name}：${title}，${action}` : `${side === 'left' ? '左鍋：' : '備用的'}空鍋，開新的一餐`}
            x={BURNER_X[side]} y={POT_Y} width={POT_W} k={0.7}
            className={styles.pot} disabled={list.isPending || busy} onClick={() => openPot(pot)}
          >
            <Pot view="side" state={pot.state} />
            {!list.isPending && <span className={styles.tag}><b>{title}</b><small>{action}</small></span>}
          </Hotspot>
        )
      })}

      <FridgeNotes adopted={notes.adopted} week={notes.week} onOpenAdopted={openAdopted} onOpenWeek={openWeek} />
    </Room>
  )
}
