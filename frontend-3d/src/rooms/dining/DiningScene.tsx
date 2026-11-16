/**
 * 餐桌（/plan/:id/result/:version；DESIGN.md 5.6）：一桌菜＝一份生成結果。
 *
 * - 轉盤上每個器皿是一道菜（飯桶是主食），桌邊每個座位是一位成員，飯碗的樣子是他的營養判定。
 * - 收據是價格（要買的整包／這餐吃掉，兩個數字不相加），小卡盤是營養小知識，立牌是全家合計。
 * - 點任何一樣東西會打開面板：桌機在桌子左右兩側，手機從下方滑出。一次只開一個。
 * - 底部動作列：採用這桌、換一桌、重新查價、重試小知識；剛採用完換成一鍵購入與離開的路。
 *   從紀錄進來看已採用的桌子（快照）沒有動作列，只能看；一鍵購入在收據明細裡。
 * - 手機這一景不顯示底部分頁，最底下是動作列。
 * - 只有「最新的那一版、而且問卷還沒採用」可以採用、決定、重試；舊版與已採用的問卷只能看
 *   （舊前端只顯示最新版；後端不擋舊版本，所以由這裡守住，判斷在 tableModel.resultFlags）。
 * - 問卷「已採用」不等於「這一桌被採用」：只有確定採用的就是這一版，才寫已採用（tableModel.adoptionOf）。
 *
 * 行為（端點、版本與冪等鍵、能不能採用的條件）照舊前端的 MealResultView 與 GenerationPanel；資料與命令在 useMealResult。
 * 能不能採用由後端判定，這裡只照 eligibility 顯示。
 * 「換一桌」＝舊前端的「重新生成」（同一份問卷再送一次）；後端沒有「換菜原因」的欄位，所以不問原因。
 */
import { Fragment, useState, type ComponentProps, type ReactNode } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router'
import { errorMessage, errorStatus } from '../../api/client'
import { MEAL_LABELS } from '../../api/types'
import z0 from '../../assets/rooms/dining/z0.svg'
import z1 from '../../assets/rooms/dining/z1.svg'
import z2 from '../../assets/rooms/dining/z2.svg'
import z3 from '../../assets/rooms/dining/z3.svg'
import { Room, useRoom, type HotspotItem } from '../../camera/Room'
import type { Stop } from '../../camera/framing'
import { Button } from '../../components/Button'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { HudNote } from '../../components/HudNote'
import { ErrorNotice, Notice } from '../../components/Notice'
import { Panel } from '../../components/Panel'
import { Spinner } from '../../components/Spinner'
import { StatusChip } from '../../components/StatusChip'
import { ratioOf } from '../../components/nutritionVerdict'
import { SceneHud } from '../../shell/SceneHud'
import type { HudTab } from '../../shell/hudLayout'
import { DishDetail, StapleDetail } from './panels/DishPanel'
import { FamilyDetail, PersonDetail } from './panels/NutritionPanels'
import { ReceiptDetail } from './panels/ReceiptPanel'
import { StatusDetail } from './panels/StatusPanel'
import { TipsDetail } from './panels/TipsPanel'
import { budgetStamp, monthDay, parseVersion } from './resultText'
import { TABLE_SPAN } from './tableLayout'
import { Tabletop } from './Tabletop'
import {
  buildSeats, buildVessels, failedOnVersion, panelSide, resultFlags, statusSummary, tableActions, targetId, tipsState, verdictLabel,
  type PanelTarget, type TableAction,
} from './tableModel'
import { useMarketCheckout, useMealResult } from './useMealResult'
import styles from './DiningScene.module.css'

const LAYERS = [z0, z1, z2, z3]
// 手機一屏要看得到整桌（含左右兩側座位的名牌），所以鏡頭拉遠；原因見 TABLE_SPAN
const STOPS: Stop[] = [{ id: 'table', label: '餐桌', fx: 0.5, span: TABLE_SPAN }]
const TIPS_STATUS = { available: '', unavailable: '暫時拿不到', retrying: '重試中', not_found: '沒有' } as const

export default function DiningScene() {
  const { id = '', version } = useParams()
  // 換一份問卷就整個重來（打開的面板、冪等鍵都不沿用）；同一份問卷換版本時留著，重試完成換版本才不會閃一下
  return <DiningTable key={id} requestId={id} version={parseVersion(version)} />
}

/** 手機這一景不顯示底部分頁（DESIGN.md 5.6）；桌機照常有側欄。是不是手機要在 <Room> 裡面才問得到。 */
function TableHud({ tab, actions, children }: { tab: HudTab; actions: ReactNode; children: ReactNode }) {
  const { phone } = useRoom()
  return (
    <SceneHud tab={tab} nav={phone ? 'none' : 'tabs'} actions={actions} actionsLabel="這一桌可以做的事">
      {children}
    </SceneHud>
  )
}

/**
 * 這一景的面板：桌機放在桌子左右的空位，寬度跟著空位縮（筆電螢幕上共用面板的 28vw 會蓋到桌面；見樣式的 .panelLeft）。
 * 手機照共用的 <Panel>，是下方抽屜。
 */
function SidePanel({ side, ...rest }: Omit<ComponentProps<typeof Panel>, 'side' | 'className'> & { side: 'left' | 'right' }) {
  return <Panel side={side} className={side === 'left' ? styles.panelLeft : styles.panelRight} {...rest} />
}

type SceneNote = { key: string; node: ReactNode }

/**
 * 桌上有菜之後的場景提示。手機上桌子只佔畫面中間那一塊，提示一多就把上面的座位、立牌和菜蓋住，
 * 所以手機一次只露出最要緊的一則（呼叫端已經照嚴重程度排好），其餘收在「還有 N 則」後面。
 * 桌機的提示放在桌子旁邊的角落，不蓋桌面，全部列出。
 */
function NoteStack({ items, className }: { items: readonly SceneNote[]; className: string }) {
  const { phone } = useRoom()
  const [open, setOpen] = useState(false)
  if (items.length === 0) return null
  const shown = phone && !open ? items.slice(0, 1) : items
  return (
    <div className={className} data-expanded={phone && open ? '' : undefined} data-no-swipe>
      {shown.map((item) => <Fragment key={item.key}>{item.node}</Fragment>)}
      {phone && items.length > 1 && (
        <button type="button" className={styles.moreNotes} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? '收起提示' : `還有 ${items.length - 1} 則提示`}
        </button>
      )}
    </div>
  )
}

function DiningTable({ requestId, version }: { requestId: string; version: number | null }) {
  const navigate = useNavigate()
  const state = useMealResult(requestId, version)
  const market = useMarketCheckout(state.adoptedPlanId)
  const [panel, setPanel] = useState<PanelTarget | null>(null)
  const [asking, setAsking] = useState(false)
  const {
    request, cooking, list, result, latestVersion, decide, retryPrice, retryKnowledge, adopt, regenerate, mealDate, mealType, adoption,
  } = state

  // 網址上的版本不是數字：有結果就帶到最新的那一版
  if (version === null && latestVersion !== undefined) {
    return <Navigate to={`/plan/${requestId}/result/${latestVersion}`} replace />
  }

  const r = result.data
  const shownVersion = r?.result_version
  const flags = r ? resultFlags(r, latestVersion) : null
  const vessels = r ? buildVessels(r) : []
  const seats = r ? buildSeats(r.nutrition) : []
  const tips = r ? tipsState(r) : null
  const family = {
    energy: ratioOf(r?.nutrition.energy_kcal, r?.nutrition.target?.energy_kcal),
    protein: ratioOf(r?.nutrition.protein_g, r?.nutrition.target?.protein_g),
  }
  const summary = r && flags ? statusSummary(r, flags, adoption) : null
  const confirmed = flags?.confirmed ?? false
  // 只能看：問卷已採用，或這一版已經被新的版本取代
  const readOnly = flags?.readOnly ?? false
  const newerVersion = flags?.newerVersion ?? null
  // 剛剛在這個畫面上採用的就是現在看的這一版（不是從紀錄進來看快照）。
  // 要比對版本：同一份問卷換版本時命令的狀態還留著，不比對的話採用第 3 版後去看第 2 版也會寫「已採用這一桌」
  const adoptedHere = adopt.isSuccess && shownVersion !== undefined && adopt.variables?.result_version === shownVersion
  const canShop = confirmed && state.hasShortages && !!state.adoptedPlanId
  const recordsTo = `/records?tab=adopted${mealDate ? `&date=${mealDate}` : ''}`
  const versionTo = (v: number) => `/plan/${requestId}/result/${v}`
  const mealLabel = mealDate ? `${monthDay(mealDate)}${mealType ? ` ${MEAL_LABELS[mealType]}` : ''}` : null

  const empty = list.data?.length === 0
  // 這一版不存在（例如網址打錯），但這份問卷有別的版本
  const missingVersion = !empty && result.isError && errorStatus(result.error) === 404
  const loadError = list.error ?? (empty || missingVersion ? null : result.error)
  const loading = !loadError && !empty && !missingVersion && (list.isPending || (version !== null && result.isPending))

  const hotspots: HotspotItem[] = r && tips ? [
    ...vessels.map((v) => ({
      id: v.id, label: v.name, stop: 'table',
      status: v.problem ? '份量未知' : v.highSodium ? '高鈉' : v.specified ? '指定菜' : v.target.kind === 'staple' ? '主食' : undefined,
      onActivate: () => setPanel(v.target),
    })),
    ...seats.map((s) => ({
      id: s.id, label: s.name, status: s.verdictLabel, stop: 'table',
      onActivate: () => setPanel({ kind: 'person', memberId: s.memberId }),
    })),
    { id: 'family', label: '家庭合計', status: verdictLabel(family.energy, family.protein), stop: 'table', onActivate: () => setPanel({ kind: 'family' }) },
    {
      id: 'receipt', label: '收據', stop: 'table', onActivate: () => setPanel({ kind: 'receipt' }),
      // 預算比的是這餐吃掉的那一個金額，寫完整的那一句（不是「整包也在預算內」）
      status: budgetStamp(r.prices)?.full ?? (r.prices.budget_status === 'PRICE_INCOMPLETE' ? '價格不完整' : undefined),
    },
    {
      id: 'tips', label: '小知識', stop: 'table', onActivate: () => setPanel({ kind: 'tips' }),
      status: tips.state === 'available' ? `${tips.count} 則` : TIPS_STATUS[tips.state],
    },
  ] : []

  // ---- 底部動作列 ----
  const openMarket = () => { void market.open() }
  const marketButton = (
    <Button variant="primary" className={styles.action} busy={market.isPending} onClick={openMarket}>
      {market.isPending ? '開啟超市中…' : '一鍵購入'}
    </Button>
  )
  const actionButton = (action: TableAction, shown: NonNullable<typeof r>): ReactNode => {
    switch (action.kind) {
      case 'adopt':
        return (
          <Button variant="primary" className={styles.action} busy={adopt.isPending} disabled={action.disabled} onClick={() => adopt.mutate(shown)}>
            {adopt.isPending ? '採用中…' : '採用這桌'}
          </Button>
        )
      case 'decide':
      case 'reason':
        return (
          <Button variant="primary" className={styles.action} onClick={() => setPanel({ kind: 'status' })}>
            {action.kind === 'decide' ? '去決定' : '看原因'}
          </Button>
        )
      case 'regenerate':
        return <Button className={styles.action} disabled={action.disabled} onClick={() => setAsking(true)}>換一桌</Button>
      case 'retryPrice':
        return (
          <Button className={styles.action} busy={retryPrice.isPending} disabled={action.disabled} onClick={() => retryPrice.mutate(shown)}>
            {flags?.priceRetrying ? '查價中…' : '重新查價'}
          </Button>
        )
      case 'retryKnowledge':
        return (
          <Button className={styles.action} busy={retryKnowledge.isPending} disabled={action.disabled} onClick={() => retryKnowledge.mutate(shown)}>
            {flags?.knowledgeRetrying ? '重試中…' : '重試小知識'}
          </Button>
        )
      case 'latest':
        return newerVersion === null ? null : (
          <Link className={`${styles.actionLink} ${styles.action}`} to={versionTo(newerVersion)} replace draggable={false}>
            看最新的第 {newerVersion} 版
          </Link>
        )
      case 'market':
        return marketButton
      case 'home':
        return <Link className={`${styles.actionLink} ${styles.action}`} to="/today" draggable={false}>回客廳</Link>
      case 'records':
        return <Link className={`${styles.actionLink} ${styles.action}`} to={recordsTo} draggable={false}>看紀錄</Link>
    }
  }
  const actionList = r && flags
    ? tableActions(r, flags, { cooking, requestLoaded: !!request.data, adoptedHere, canShop })
    : []
  const actions = r && actionList.length > 0
    ? actionList.map((action) => <Fragment key={action.kind}>{actionButton(action, r)}</Fragment>)
    : null

  // 決定、查價、採用失敗的訊息：畫面上方和相關的面板裡都放，一定以文字呈現。
  // 只放「對現在看的這一版下的命令」的失敗：換到別的版本之後，上一版的錯誤不跟過來
  const decideFailed = failedOnVersion(decide, shownVersion, (v) => v.r.result_version)
  const retryPriceFailed = failedOnVersion(retryPrice, shownVersion, (v) => v.result_version)
  const commandError = decideFailed || retryPriceFailed
    ? <ErrorNotice error={decideFailed ? decide.error : retryPrice.error} />
    : null
  const adoptError = failedOnVersion(adopt, shownVersion, (v) => v.result_version) ? <ErrorNotice error={adopt.error} /> : null
  const knowledgeError = failedOnVersion(retryKnowledge, shownVersion, (v) => v.result_version) ? <ErrorNotice error={retryKnowledge.error} /> : null
  // 一鍵購入是對被採用的那一桌下的；看別的版本時沒有這顆按鈕，它的錯誤也不顯示
  const marketError = market.isError && canShop ? <ErrorNotice error={market.error} fallback="暫時無法開啟模擬超市，請重試。" /> : null
  // 已採用，但讀不到已採用紀錄（或讀不到問卷，不知道要找哪一天的紀錄）：
  // 不知道採用的是不是這一桌，也開不了一鍵購入——要說出來並給重試
  const adoptionLookupFailed = adoption.kind === 'unresolved' && (request.isError || state.adoptedPlans.isError)
  const retryLookup = (
    <Button
      busy={request.isFetching || state.adoptedPlans.isFetching}
      onClick={() => { void (request.isError ? request.refetch() : state.adoptedPlans.refetch()) }}
    >
      重試
    </Button>
  )

  // ---- 面板 ----
  const close = () => setPanel(null)
  // 桌機：面板開在桌子的哪一邊（手機一律是下方抽屜）
  const side = r && panel ? panelSide(panel, (r.nutrition.per_person ?? []).map((p) => p.member_id)) : 'right'
  let sheet: ReactNode = null
  if (r && flags && tips && panel) {
    if (panel.kind === 'dish') {
      const dish = r.dishes[panel.index]
      if (dish) {
        sheet = (
          <SidePanel title={dish.dish_name} side={side} onClose={close}>
            <DishDetail dish={dish} index={panel.index} readOnly={readOnly} />
          </SidePanel>
        )
      }
    } else if (panel.kind === 'staple') {
      if (r.staple) sheet = <SidePanel title={`主食：${r.staple.label}`} side={side} onClose={close}><StapleDetail staple={r.staple} /></SidePanel>
    } else if (panel.kind === 'person') {
      const person = (r.nutrition.per_person ?? []).find((p) => p.member_id === panel.memberId)
      if (person) {
        sheet = (
          <SidePanel title={`${person.name ?? '已刪除成員'}的營養`} side={side} onClose={close}>
            <PersonDetail person={person} nutrition={r.nutrition} onShowFamily={() => setPanel({ kind: 'family' })} />
          </SidePanel>
        )
      }
    } else if (panel.kind === 'family') {
      sheet = (
        <SidePanel title="全家營養" side={side} onClose={close}>
          <FamilyDetail nutrition={r.nutrition} dishCount={r.dishes.length} stapleLabel={r.staple?.label ?? null} />
        </SidePanel>
      )
    } else if (panel.kind === 'tips') {
      sheet = (
        <SidePanel title="營養小知識" side={side} onClose={close}>
          <TipsDetail
            knowledge={r.knowledge} state={tips.state} retry={r.retries?.knowledge ?? null} readOnly={readOnly}
            retryPending={retryKnowledge.isPending} retryDisabled={cooking}
            onRetry={() => retryKnowledge.mutate(r)} error={knowledgeError}
          />
        </SidePanel>
      )
    } else if (panel.kind === 'receipt') {
      const priceRetry = r.retries?.price ?? null
      sheet = (
        <SidePanel title="收據明細" side={side} onClose={close}>
          <ReceiptDetail prices={r.prices}>
            {flags.canRetryPrice && !flags.priceRetrying && (
              <Notice action={<Button busy={retryPrice.isPending} disabled={cooking} onClick={() => retryPrice.mutate(r)}>重新查價</Button>}>
                {flags.missingPriceNames.join('、')} 查無價格，成本只計已知部分；不影響採用，也可以重新查價。
              </Notice>
            )}
            {flags.priceRetrying && <Notice><Spinner label="正在向價格來源查詢缺價食材，可能需要幾分鐘；完成後會出現新版本。" /></Notice>}
            {priceRetry?.status === 'failed' && (
              <Notice tone="warn">
                上次重新查價沒有完成：{priceRetry.error?.message ?? '原因未知'}。原結果不變{readOnly ? '。' : '，可以直接採用或稍後再試。'}
              </Notice>
            )}
            {retryPriceFailed && <ErrorNotice error={retryPrice.error} />}
            {canShop && marketButton}
            {marketError}
            {adoptionLookupFailed && state.hasShortages && (
              <Notice tone="warn" action={retryLookup}>
                無法讀取已採用餐單，暫時無法開啟模擬購物車。
              </Notice>
            )}
          </ReceiptDetail>
        </SidePanel>
      )
    } else {
      sheet = (
        <SidePanel title="這桌的狀況" side={side} onClose={close}>
          <StatusDetail
            r={r} flags={flags} recordsTo={recordsTo} mealLabel={mealLabel} adoption={adoption}
            versions={list.data} versionTo={versionTo}
            cooking={cooking} decidePending={decide.isPending} retryPricePending={retryPrice.isPending}
            onDecide={(kind) => decide.mutate({ r, kind })} onRetryPrice={() => retryPrice.mutate(r)}
            onShowFamily={() => setPanel({ kind: 'family' })}
            error={<>{commandError}{adoptError}</>}
          />
        </SidePanel>
      )
    }
  }

  // ---- 場景層級的提示 ----
  const priceRetryState = r?.retries?.price ?? null
  const knowledgeRetryState = r?.retries?.knowledge ?? null
  const loadFailure = loadError != null && (
    <ErrorNotice
      error={loadError} fallback="讀不到這一桌的結果，請稍後再試。"
      onRetry={() => { void list.refetch(); if (version !== null) void result.refetch() }}
      retrying={list.isFetching || result.isFetching}
    />
  )
  // 還沒有菜（載入、失敗、空桌）時的提示：放上方中央
  const blank = (
    <>
      {loading && <Spinner label="正在上菜…" />}
      {loadFailure}
      {missingVersion && (
        <Notice tone="warn" action={latestVersion !== undefined && latestVersion !== version
          ? <Link className={styles.noteLink} to={versionTo(latestVersion)} replace>看最新的第 {latestVersion} 版</Link>
          : <Link className={styles.noteLink} to={`/plan/${requestId}`}>回問卷</Link>}>
          找不到第 {version} 版。
        </Notice>
      )}
      {empty && (
        <Notice title="這份問卷還沒有煮出結果" action={<Link className={styles.noteLink} to={`/plan/${requestId}`}>回問卷</Link>}>
          送出生成、煮好之後，菜才會端上桌。
        </Notice>
      )}
    </>
  )
  // 桌上有菜之後的提示，照嚴重程度排（錯誤 → 警告 → 說明 → 完成 → 進行中）：手機一次只露出第一則，見 <NoteStack>
  const tableNotes: SceneNote[] = []
  const note = (key: string, node: ReactNode) => { if (node) tableNotes.push({ key, node }) }
  if (r) {
    // 重讀失敗（桌上還是之前讀到的那一版）
    note('load', loadFailure)
    note('command', commandError)
    note('adopt', adoptError)
    note('knowledge', knowledgeError)
    note('market', marketError)
    note('conflict', flags?.dayConflict && !readOnly && (
      <Notice tone="error">
        {r.previous_day_check?.message}（重複：{r.previous_day_check?.conflicting_dishes?.join('、')}）
      </Notice>
    ))
    note('other', adoption.kind === 'other' && (
      <Notice tone="warn" action={<Link className={styles.noteLink} to={versionTo(adoption.version)} replace>看第 {adoption.version} 版</Link>}>
        這份問卷採用的是第 {adoption.version} 版；這是第 {r.result_version} 版，沒有被採用，只能看。
      </Notice>
    ))
    note('menu', adoption.kind === 'menu' && (
      <Notice tone="warn" action={<Link className={styles.noteLink} to={recordsTo}>看紀錄</Link>}>
        這份問卷採用的是資料庫菜單，不是這一桌；這一桌沒有被採用，只能看。
      </Notice>
    ))
    note('lookup', adoptionLookupFailed && (
      <Notice tone="warn" action={retryLookup}>
        這份問卷已經採用了，但讀不到已採用紀錄，還不確定採用的是不是這一桌。
      </Notice>
    ))
    // 重試失敗：原因要寫出來（按了重新查價、轉完圈卻什麼都沒變，會不知道發生什麼事）。只能看的桌子只在面板裡寫
    note('priceFailed', !readOnly && priceRetryState?.status === 'failed' && (
      <Notice tone="warn">
        上次重新查價沒有完成：{priceRetryState.error?.message ?? '原因未知'}。原結果不變，可以直接採用或稍後再試。
      </Notice>
    ))
    note('knowledgeFailed', !readOnly && tips?.state === 'unavailable' && knowledgeRetryState?.status === 'failed' && (
      <Notice tone="warn">
        上次重試小知識失敗：{knowledgeRetryState.error?.message ?? '原因未知'}。菜單、份量與採用資格不受影響。
      </Notice>
    ))
    // 已採用就不會再換版本，不用提醒還有更新的（newerVersion 這時是 null）
    note('newer', newerVersion !== null && (
      <Notice action={<Link className={styles.noteLink} to={versionTo(newerVersion)} replace>看最新的第 {newerVersion} 版</Link>}>
        這是第 {r.result_version} 版，已經有比較新的結果了。舊的版本只能看。
      </Notice>
    ))
    note('cooking', cooking && (
      <Notice action={<Link className={styles.noteLink} to={`/plan/${requestId}/cooking`}>去看鍋</Link>}>
        這份問卷正在重新煮，桌上是之前的結果；煮好之前不能採用。
      </Notice>
    ))
    note('adopted', adoptedHere && (
      <Notice tone="success" title="已採用這一桌">
        已保存為已採用餐食紀錄（不代表實際已食用）。
      </Notice>
    ))
    note('priceRetrying', flags?.priceRetrying && <Spinner label="正在重新查價，完成後會換成新版本…" />)
    note('knowledgeRetrying', flags?.knowledgeRetrying && <Spinner label="小知識重試中，完成後會換成新版本…" />)
  }

  // 已採用（不管是不是剛剛採用的）上一層是紀錄；還沒採用是問卷
  const snapshot = confirmed && !adoptedHere
  return (
    <Room
      name="餐桌" layers={LAYERS} stops={STOPS} hotspots={hotspots}
      hud={(
        <TableHud tab={snapshot ? 'records' : 'plan'} actions={actions}>
          <div className={styles.header} data-no-swipe>
            <Link
              className={styles.back} to={confirmed ? recordsTo : `/plan/${requestId}`} draggable={false}
              aria-label={confirmed ? '查看已採用紀錄' : '回到這份問卷'}
            >
              <span aria-hidden="true">‹ </span>{confirmed ? '紀錄' : '問卷'}
            </Link>
            {r && summary && (
              <>
                <span className={styles.chips}>
                  <StatusChip tone={r.mode === 'ai_generated_menu' ? 'info' : 'neutral'}>{r.mode_label}</StatusChip>
                  <StatusChip>第 {r.result_version} 版</StatusChip>
                </span>
                <button
                  type="button" className={`${styles.pill} ${styles[summary.tone]}`}
                  aria-expanded={panel?.kind === 'status'} onClick={() => setPanel({ kind: 'status' })}
                >
                  <span className={styles.pillText}>
                    <span className="visually-hidden">這桌的狀況：</span>
                    {summary.text}
                    {/* 桌角的「已採用 11/14 晚餐」；手機上方放不下，日期寫在面板裡 */}
                    {adoption.kind === 'this' && mealLabel && <span className={styles.when}>・{mealLabel}</span>}
                  </span>
                  {/* 手機上方放不下模式的標籤：AI 生成的菜單至少留一個記號，完整的說明在面板裡 */}
                  {r.mode === 'ai_generated_menu' && (
                    <span className={styles.modeMark}>AI<span className="visually-hidden">（{r.mode_label}）</span></span>
                  )}
                  <span aria-hidden="true"> ›</span>
                </button>
                {/* 桌機上桌緣的小卡盤不起眼，上方再放一個入口；手機的物件列裡本來就有 */}
                {tips?.state === 'available' && (
                  <button
                    type="button" className={`${styles.pill} ${styles.tipsChip}`}
                    aria-expanded={panel?.kind === 'tips'} onClick={() => setPanel({ kind: 'tips' })}
                  >
                    營養小知識 {tips.count} 則<span aria-hidden="true"> ›</span>
                  </button>
                )}
              </>
            )}
          </div>

          {/*
            還沒有菜（載入、失敗、空桌）：提示在上方中央。桌上有菜之後，上方中央是坐在上面那位的名牌，
            所以桌機改放桌子旁邊的角落（預設左上；左邊開著面板時換到右上，才不會被面板蓋住）。樣式見 .notes。
          */}
          {r
            ? <NoteStack items={tableNotes} className={`${styles.notes} ${sheet && side === 'left' ? styles.notesRight : styles.notesLeft}`} />
            : <HudNote>{blank}</HudNote>}

          {/* 換成另一個面板時重新掛上：焦點才會移進新的面板，內容也從最上面開始 */}
          {sheet && panel && <Fragment key={targetId(panel)}>{sheet}</Fragment>}

          {asking && request.data && (
            request.data.complete ? (
              <ConfirmDialog
                title="要換一桌嗎？"
                confirmLabel={regenerate.isPending ? '送出中…' : state.regenerateFailure ? '再煮一次' : '重新煮一桌'}
                busy={regenerate.isPending}
                error={regenerate.isError ? errorMessage(regenerate.error)
                  : state.regenerateFailure ? `這次沒有煮成：${state.regenerateFailure}` : undefined}
                onConfirm={() => regenerate.mutate(request.data)}
                onCancel={() => { setAsking(false); regenerate.reset() }}
              >
                會用同一份問卷重新煮一桌，現在這一版仍會保留。想調整條件（例如預算或指定其他菜色），請先回問卷修改再送出。
              </ConfirmDialog>
            ) : (
              <ConfirmDialog
                title="問卷還沒填完" confirmLabel="回問卷" onConfirm={() => navigate(`/plan/${requestId}`)}
                onCancel={() => setAsking(false)}
              >
                這份問卷後來改過、還有項目沒填，補齊之後才能重新煮一桌。
              </ConfirmDialog>
            )
          )}
        </TableHud>
      )}
    >
      {r && tips && (
        <Tabletop
          vessels={vessels} seats={seats} family={family} prices={r.prices} tips={tips}
          activeId={panel ? targetId(panel) : null} onOpen={setPanel}
        />
      )}
    </Room>
  )
}
