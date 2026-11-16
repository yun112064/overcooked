/**
 * 單板＋鍋（/plan/:id、/plan/:id/cooking）：問卷與生成（DESIGN.md 5.4、5.5）。
 *
 * - 問卷寫在夾在單板上的那張紙上，共五步；每換一步就把草稿存到後端（PUT，帶版本號）。
 * - 右邊的鍋是進度：每完成一步多一樣料，五步都好就全滿；送出後蓋上鍋蓋，旁邊列出後端回報的階段。
 * - 桌機：紙在左、鍋在右。手機：鏡頭只看鍋，問卷是下方可以收起的抽屜，沒有底部分頁。
 * - /cooking 是同一個場景的另一個網址（生成中）；煮好了鏡頭轉到餐桌，取消或失敗回到第 5 步。
 *   桌機生成中，階段列貼在鍋的右邊；平板與手機看不到那一塊，階段列寫在紙上／抽屜裡。
 *
 * 行為照舊前端的問卷編輯頁（MealRequestEditor）：
 * 只檢查格式就能換步驟（沒填完也能往下，草稿不會丟）；內容沒變不存（回看問卷不會讓已生成的結果退回草稿）；
 * 版本衝突（409）請使用者重新載入；生成中與已採用只能看。食譜庫選菜的問卷轉到 /pick/:id。
 * 和舊頁面不同的一點：問卷打開之後，背景重讀失敗不會把它換成錯誤畫面（見 gate.ts）。
 */
import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { useForm, useWatch, type FieldPath } from 'react-hook-form'
import { Link, Navigate, useMatch, useNavigate, useParams, useSearchParams } from 'react-router'
import { api, apiError, errorMessage, errorStatus } from '../../api/client'
import { MEAL_LABELS, type MealRequest, type MealRequestDraft, type Member } from '../../api/types'
import z0 from '../../assets/rooms/passboard/z0.svg'
import z1 from '../../assets/rooms/passboard/z1.svg'
import z2 from '../../assets/rooms/passboard/z2.svg'
import z3 from '../../assets/rooms/passboard/z3.svg'
import { Placed, Room } from '../../camera/Room'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { HudNote } from '../../components/HudNote'
import { requestStatusInfo } from '../../components/requestStatus'
import { ErrorNotice, Notice } from '../../components/Notice'
import { Spinner } from '../../components/Spinner'
import { Pot } from '../../pot/Pot'
import { potLabel, potState } from '../../pot/potState'
import { mealRequestQuery, mealRequestsQuery, membersQuery } from '../../session/queries'
import { SceneHud } from '../../shell/SceneHud'
import { CookingView, GenerationBlock, StageList, Summary } from './Confirm'
import {
  LAST_STEP, STEPS, STEP_FIELDS, formFieldOf, initialStep, issuesForStep, potSteps, requestedStep, schema, stepsWithIssues,
  toDraft, toForm,
  type FormValues,
} from './draft'
import { gate } from './gate'
import { isActive, stageLabel, submitState } from './generation'
import {
  PAPER, POT, POT_TAG, STAGE_CARD, artHeightPercent, layoutFor, mascotFor, paperArea, stageCardTop,
  type Layout, type SheetSize,
} from './layout'
import { AdoptedMenu } from './AdoptedMenu'
import { StepIssues, StepMembers, StepPantry, StepTaste, StepWhen } from './Steps'
import { useGeneration } from './useGeneration'
import paper from './Paper.module.css'
import styles from './PassboardScene.module.css'

const LAYERS = [z0, z1, z2, z3]
/** 煮好後先讓鍋蓋開著一下，再把鏡頭帶到餐桌 */
const OPEN_LID_MS = 1200
/** 抽屜收起時留在畫面底部的高度（px）；和 PassboardScene.module.css 的 .sheet[data-size='peek'] 是同一個數字 */
const PEEK_PX = 68

/** 會叫出螢幕鍵盤的欄位（勾選框、日期、選檔案不算） */
const TYPING = 'textarea, input:not([type="checkbox"]):not([type="file"]):not([type="date"])'

/** 畫面大小（和 <Room> 量的是同一塊：蓋滿視窗的固定區域）。 */
function useViewport() {
  const [size, setSize] = useState(() => ({ w: document.documentElement.clientWidth, h: document.documentElement.clientHeight }))
  useEffect(() => {
    const measure = () => {
      const w = document.documentElement.clientWidth
      const h = document.documentElement.clientHeight
      if (w > 0 && h > 0) setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }))
    }
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])
  return size
}

function BackLink() {
  return <Link className={styles.back} to="/plan" draggable={false}><span aria-hidden="true">‹ </span>回廚房</Link>
}

/** 還沒讀到問卷（或讀取失敗）時：只有場景和一句話。 */
function EmptyRoom({ layout, children }: { layout: Layout; children: ReactNode }) {
  return (
    <Room
      name="單板與鍋" layers={LAYERS} stops={[layout.stop]}
      hud={(
        <SceneHud tab="plan" nav={layout.panning ? 'none' : 'tabs'}>
          <BackLink />
          <HudNote>{children}</HudNote>
        </SceneHud>
      )}
    >
      <Placed x={POT.x} y={POT.y} width={POT.width} k={0.7}><Pot view="top" state={{ kind: 'empty' }} /></Placed>
    </Room>
  )
}

export default function PassboardScene() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const view = useViewport()
  const layout = layoutFor(view.w, view.h)
  const request = useQuery(mealRequestQuery(id))
  const members = useQuery(membersQuery)

  // 登入過期或還沒選家庭（例如開發伺服器重啟）：回到大門或玄關，不停在一個什麼都讀不到的房間
  const failure = request.error ?? members.error
  useEffect(() => {
    if (!failure) return
    if (errorStatus(failure) === 401) void queryClient.invalidateQueries({ queryKey: ['me'] })
    else if (apiError(failure)?.code === 'HOUSEHOLD_NOT_SELECTED') navigate('/households', { replace: true })
  }, [failure, navigate, queryClient])

  // 這張單的問卷打開過了沒（記 id：同一個畫面換到另一張單時要重新等資料）
  const [opened, setOpened] = useState<string | null>(null)
  const show = gate({
    loaded: !!request.data && !!members.data,
    failed: request.isError || members.isError,
    fetchedAfterMount: request.isFetchedAfterMount && members.isFetchedAfterMount,
    opened: opened === id,
  })
  if (show === 'editor' && opened !== id) setOpened(id)

  if (show === 'error') {
    const retry = () => { void request.refetch(); void members.refetch() }
    return (
      <EmptyRoom layout={layout}>
        <ErrorNotice error={failure} fallback="讀不到這張單，請稍後再試。" onRetry={retry} retrying={request.isFetching || members.isFetching} />
      </EmptyRoom>
    )
  }
  if (show === 'loading' || !request.data || !members.data) {
    return <EmptyRoom layout={layout}><Spinner label="正在把單夾上單板…" /></EmptyRoom>
  }
  if (request.data.draft.mode === 'picker') return <Navigate to={`/pick/${request.data.id}`} replace />
  // key：換問卷時重建表單狀態
  return <Editor key={request.data.id} initial={request.data} members={members.data} view={view} layout={layout} />
}

type EditorProps = {
  initial: MealRequest
  members: Member[]
  view: { w: number; h: number }
  layout: Layout
}

/** 問卷本體。另外匯出是為了測試：可以不經過讀取畫面，直接給一份問卷看畫出來的內容。 */
export function Editor({ initial, members, view, layout }: EditorProps) {
  const id = initial.id
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const onCooking = useMatch('/plan/:id/cooking') !== null

  const [server, setServer] = useState<MealRequest>(initial)
  // ?step=N：別的房間指定先到哪一步（冰箱把手帶 step=3，先拍家裡有的食材）；只在進來時讀一次
  const [params] = useSearchParams()
  const [step, setStep] = useState(() => (
    onCooking ? LAST_STEP : requestedStep(params.get('step'), initial.status) ?? initialStep(initial.status)
  ))
  // 這次進來走到過的最後一步：決定第 3 步（可略過）算不算完成，以及哪些步驟要標「還缺」
  const [furthest, setFurthest] = useState(step)
  const [sheet, setSheet] = useState<SheetSize>('half')
  const [askCancel, setAskCancel] = useState(false)
  const [refreshFailed, setRefreshFailed] = useState(false)
  // 剛煮好、正要去餐桌的結果版本
  const [served, setServed] = useState<number | null>(null)

  const form = useForm<FormValues>({ mode: 'onTouched', resolver: zodResolver(schema), defaultValues: toForm(initial, members) })
  const { trigger, getValues, setError, reset, formState: { errors } } = form
  const available = useWatch({ control: form.control, name: 'available' })
  // 上次儲存的草稿內容：切換步驟時內容沒變就不儲存（回看問卷不會讓已生成的結果退回草稿）
  const lastSaved = useRef(JSON.stringify(toDraft(toForm(initial, members))))

  const adoptRow = useCallback((row: MealRequest) => {
    setServer(row)
    queryClient.setQueryData(mealRequestQuery(id).queryKey, row)
    // 廚房與客廳的鍋、單軌讀的是清單
    void queryClient.invalidateQueries({ queryKey: mealRequestsQuery.queryKey })
  }, [id, queryClient])

  const save = useMutation({
    mutationFn: async (draft: MealRequestDraft) =>
      (await api.put<MealRequest>(`/meal-requests/${id}`, { version: server.version, draft })).data,
    onSuccess: (row, draft) => {
      lastSaved.current = JSON.stringify(draft)
      adoptRow(row)
    },
    onError: (err) => {
      // 後端指出是哪個欄位的問題：標在那個欄位上（步驟條也會跟著標）
      const e = apiError(err)
      const fields = e?.details?.fields ?? (e?.details?.field ? [{ field: e.details.field, message: e.message }] : [])
      for (const f of fields) {
        const key = formFieldOf(f.field)
        if (key) setError(key as FieldPath<FormValues>, { message: f.message })
      }
    },
  })

  // 送出、取消、採用後重新讀取問卷狀態（生成中不能修改、已採用只能看）
  const refresh = useCallback(async () => {
    try {
      adoptRow((await api.get<MealRequest>(`/meal-requests/${id}`)).data)
      setRefreshFailed(false)
    } catch {
      setRefreshFailed(true)
    }
  }, [adoptRow, id])

  const leaveTimer = useRef<number | undefined>(undefined)
  // 已經排好要去餐桌：下面「沒有在煮就回問卷」的檢查不要搶先把網址換掉
  const leaving = useRef(false)
  useEffect(() => () => window.clearTimeout(leaveTimer.current), [])

  const generation = useGeneration({
    row: server,
    onChanged: () => { void refresh() },
    onSettled: (job) => {
      void refresh()
      // 工作已經結束，沒有東西可以取消了
      setAskCancel(false)
      const version = job?.status === 'succeeded' ? job.result_summary?.result_version ?? job.result_version : null
      if (version != null) {
        setServed(version)
        // 看著鍋的時候煮好：鍋蓋打開，稍後鏡頭轉到餐桌。
        // 正在回看問卷就不打斷，只在問卷上方留一張「煮好了」的便條。
        if (onCooking) {
          leaving.current = true
          leaveTimer.current = window.setTimeout(() => navigate(`/plan/${id}/result/${version}`), OPEN_LID_MS)
        }
        return
      }
      // 取消或失敗：料留在鍋裡，回到第 5 步（原因與「再煮一次」在那裡）
      setStep(LAST_STEP)
      setSheet((size) => (size === 'peek' ? 'half' : size))
      navigate(`/plan/${id}`, { replace: true })
    },
  })
  const { job, active } = generation

  const locked = server.status === 'queued' || server.status === 'running'
  const adopted = server.status === 'confirmed'
  const readOnly = locked || adopted

  // 開著 /cooking 但其實沒有在煮（直接貼網址、或工作早就結束）：回到問卷
  const jobLoaded = !generation.latest.isPending
  const nothingCooking = onCooking && jobLoaded && !active && !locked && served === null
  useEffect(() => {
    if (nothingCooking && !leaving.current) navigate(`/plan/${id}`, { replace: true })
  }, [nothingCooking, navigate, id])

  const scroller = useRef<HTMLDivElement>(null)
  /** 錯誤訊息寫在問卷最上面；按鈕在最下面，所以出錯時把內容捲回頂端，訊息才看得到 */
  const showTop = () => scroller.current?.scrollTo({ top: 0 })

  const goTo = async (next: number) => {
    const target = Math.min(Math.max(next, 0), LAST_STEP)
    const arrive = () => {
      setStep(target)
      setFurthest((prev) => Math.max(prev, target))
    }
    // 生成中或已採用只能瀏覽，不儲存
    if (readOnly) { arrive(); return }
    // 只檢查格式；未填完也能前往下一步，草稿不會遺失
    if (!(await trigger(STEP_FIELDS[step]))) return
    const draft = toDraft(getValues())
    // 內容沒變（例如按上一步回看）就不儲存
    if (JSON.stringify(draft) === lastSaved.current) { arrive(); return }
    try {
      await save.mutateAsync(draft)
      arrive()
    } catch {
      // 後端指出是「要去的那一步」的欄位有問題時仍然讓使用者過去改，
      // 不然每次離開都先存、存了又被擋，會卡在這一步出不去。
      if (target !== step && STEP_FIELDS[target].some((field) => form.getFieldState(field).invalid)) arrive()
      else showTop()
    }
  }

  // 版本衝突後「重新載入」：拿後端最新的內容重建表單（還沒存的修改會被蓋掉，和重新整理頁面一樣）
  const reload = useMutation({
    mutationFn: async () => (await api.get<MealRequest>(`/meal-requests/${id}`)).data,
    onSuccess: (row) => {
      const values = toForm(row, members)
      reset(values)
      lastSaved.current = JSON.stringify(toDraft(values))
      adoptRow(row)
      setRefreshFailed(false)
      save.reset()
    },
  })

  const submitMeal = (regenerate: boolean) => {
    generation.submit.mutate(regenerate, {
      onSuccess: (submitted) => { if (isActive(submitted)) navigate(`/plan/${id}/cooking`) },
      onError: showTop,
    })
  }

  // 換步驟後把焦點帶到新步驟的標題，內容捲回最上面（第一次畫出來時不搶焦點）
  const heading = useRef<HTMLHeadingElement>(null)
  const shownStep = useRef(step)
  useEffect(() => {
    if (shownStep.current === step) return
    shownStep.current = step
    scroller.current?.scrollTo({ top: 0 })
    heading.current?.focus({ preventScroll: true })
  }, [step])

  // ---- 鍋 ----
  const pot = potState({ request: server, job, stepsDone: potSteps(server, furthest) })
  const potText = pot.kind === 'cooking' && job && isActive(job) ? `${potLabel(pot)}・${stageLabel(job)}`
    : pot.kind === 'failed' ? '沒煮成・原因在第 5 步'
      : potLabel(pot)

  // ---- 步驟條 ----
  // 生成中與已採用只能看，不提醒要補什麼
  const serverMarks = stepsWithIssues(readOnly ? [] : server.issues, furthest)
  const marks = STEPS.map((_, i) => serverMarks[i] || STEP_FIELDS[i].some((field) => field in errors))
  const conflict = save.isError && errorStatus(save.error) === 409
  const cook = submitState(server, job)
  // 草稿照舊寫「草稿第 N 版」；送出之後的狀態（失敗、已有結果、已採用…）不再叫它草稿
  const versionLabel = server.status === 'draft'
    ? `草稿第 ${server.version} 版`
    : `${requestStatusInfo(server.status).label}・第 ${server.version} 版`
  const cooking = onCooking && (active || locked || !jobLoaded || served !== null)
  // 回看問卷時煮好了（沒有自動轉到餐桌）：第 1–4 步上方留一張便條；第 5 步本來就有「看這一桌」
  const servedNote = served !== null && !cooking && step < LAST_STEP
    && (server.status === 'result_ready' || server.status === 'awaiting_decision')

  // ---- 版面：紙（桌機）或抽屜（手機，或視窗扁到紙放不下） ----
  const area = layout.phone ? null : paperArea(view.w, view.h, layout.stop)
  const drawer = area === null
  // 生成中、整個世界都看得到（右爐口在畫面內）：階段列貼在鍋旁，紙上就不再列一次
  const stagesByPot = cooking && served === null && !drawer && !layout.panning && job !== null && isActive(job)

  const date = server.draft.meal_date
  const title = date && server.draft.meal_type
    ? `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))} ${MEAL_LABELS[server.draft.meal_type]}`
    : '新的一餐'

  const body = (
    <section className={styles.form} aria-label={`${title}的問卷`}>
      <header className={styles.head}>
        <div className={styles.headLine}>
          <h2>{title}</h2>
          <small role="status">
            {cooking ? '已送出' : `${versionLabel}${save.isPending ? '・儲存中…' : ''}`}
          </small>
        </div>
        {!cooking && (
          <ol className={styles.rail} aria-label="問卷步驟">
            {STEPS.map((item, i) => (
              <li key={item.short}>
                <button
                  type="button" aria-current={i === step ? 'step' : undefined} disabled={save.isPending}
                  data-state={i === step ? 'current' : i < step ? 'done' : 'todo'}
                  onClick={() => { if (i !== step) void goTo(i) }}
                >
                  <i aria-hidden="true">{i + 1}</i>
                  <span><span className="visually-hidden">第 {i + 1} 步：</span>{item.short}</span>
                  {marks[i] && <em><span className="visually-hidden">（還有要補的）</span></em>}
                </button>
              </li>
            ))}
          </ol>
        )}
      </header>

      <div className={styles.scroll} ref={scroller}>
        {refreshFailed && (
          <Notice
            tone="warn" title="讀不到最新的狀態"
            action={<button type="button" className={paper.smallButton} onClick={() => { void refresh() }}>再讀一次</button>}
          >
            畫面上可能是舊的內容。
          </Notice>
        )}
        {cooking ? (
          <>
            <h3 ref={heading} tabIndex={-1}>{served !== null ? '煮好了！' : '正在煮…'}</h3>
            {served !== null ? (
              <div className={paper.stack}>
                <p>鍋蓋打開了，這就端上桌。</p>
                <p><Link className={`${paper.smallButton} ${paper.smallPrimary}`} to={`/plan/${id}/result/${served}`}>看這一桌</Link></p>
              </div>
            ) : (
              <CookingView row={server} generation={generation} showStages={!stagesByPot} onAskCancel={() => setAskCancel(true)} />
            )}
          </>
        ) : (
          <>
            {conflict && (
              <Notice
                tone="warn"
                action={(
                  <button type="button" className={paper.smallButton} disabled={reload.isPending} onClick={() => reload.mutate()}>
                    {reload.isPending ? '載入中…' : '重新載入'}
                  </button>
                )}
              >
                {errorMessage(save.error)}
              </Notice>
            )}
            {reload.isError && <Notice tone="error">{errorMessage(reload.error)}</Notice>}
            {save.isError && !conflict && <Notice tone="error">{errorMessage(save.error)}</Notice>}
            {generation.submit.isError && <Notice tone="error" title="沒有送出去">{errorMessage(generation.submit.error)}</Notice>}
            {locked && (
              <Notice action={<Link className={paper.smallButton} to={`/plan/${id}/cooking`}>看鍋裡的進度</Link>}>
                這張單正在煮，完成前無法修改；等不及可以在「確認」步驟取消生成。
              </Notice>
            )}
            {adopted && <Notice>這張單已經採用，只能瀏覽；如需調整請回廚房開一張新的單。</Notice>}
            {servedNote && (
              <Notice
                tone="success" title="煮好了！"
                action={<Link className={`${paper.smallButton} ${paper.smallPrimary}`} to={`/plan/${id}/result/${served}`}>看這一桌</Link>}
              >
                剛剛那一鍋煮好了。再修改問卷並儲存的話，這張單會回到草稿。
              </Notice>
            )}

            <h3 ref={heading} tabIndex={-1}>
              <small>步驟 {step + 1} / {STEPS.length}</small>{step === LAST_STEP && adopted ? '這張單的內容' : STEPS[step].title}
            </h3>
            {step < LAST_STEP && !readOnly && <StepIssues issues={issuesForStep(server.issues, step, furthest)} />}
            {step === 0 && <StepWhen form={form} row={server} readOnly={readOnly} />}
            {step === 1 && <StepMembers form={form} row={server} readOnly={readOnly} members={members} />}
            {step === 2 && <StepPantry form={form} row={server} readOnly={readOnly} />}
            {step === 3 && <StepTaste form={form} row={server} readOnly={readOnly} />}
            {step === LAST_STEP && (
              <div className={paper.stack}>
                <Summary row={server} members={members} onGoTo={readOnly ? undefined : (target) => { void goTo(target) }} />
                <hr className={paper.rule} />
                {/* 菜單要按「開始煮」才端上餐桌；這裡只在採用之後列出保存的那一份 */}
                {adopted && <><AdoptedMenu row={server} /><hr className={paper.rule} /></>}
                <GenerationBlock
                  row={server} generation={generation}
                  onRegenerate={() => submitMeal(true)} onAskCancel={() => setAskCancel(true)}
                />
              </div>
            )}
          </>
        )}
      </div>

      {!cooking && (
        <footer className={styles.foot}>
          <button type="button" className={paper.smallButton} disabled={step === 0 || save.isPending} onClick={() => { void goTo(step - 1) }}>
            上一步
          </button>
          <button type="button" className={paper.smallButton} disabled={save.isPending || readOnly} onClick={() => { void goTo(step) }}>
            儲存草稿
          </button>
          {step < LAST_STEP && (
            <button
              type="button" className={`${paper.smallButton} ${paper.smallPrimary} ${styles.next}`}
              disabled={save.isPending} onClick={() => { void goTo(step + 1) }}
            >
              {save.isPending ? '儲存中…' : step === 2 && available.length === 0 ? '略過這一步' : '下一步'}
            </button>
          )}
          {/* 最後一步的主要動作固定在這一排：摘要和預覽再長，也不用捲到底才找得到 */}
          {step === LAST_STEP && cook.showSubmit && (
            <button
              type="button" className={`${paper.smallButton} ${paper.smallPrimary} ${styles.next}`}
              disabled={!cook.canSubmit || generation.submit.isPending || save.isPending}
              aria-busy={generation.submit.isPending || undefined} onClick={() => submitMeal(false)}
            >
              {generation.submit.isPending ? '送出中…' : cook.submitLabel}
            </button>
          )}
        </footer>
      )}
    </section>
  )

  const sheetOpen = sheet !== 'peek'
  const mascot = mascotFor(drawer, layout.phone, sheet)

  return (
    <Room
      name="單板與鍋" layers={LAYERS} stops={[layout.stop]}
      bottomInset={drawer && !sheetOpen ? PEEK_PX : 0}
      hud={(
        // 外面這一層不佔版面，只用來告訴吉祥物的跑道要站多高（樣式在 .hud）
        <div className={styles.hud} data-mascot={mascot.lifted ? 'lifted' : undefined}>
          <SceneHud tab="plan" nav={layout.panning ? 'none' : 'tabs'} assistant={mascot.assistant}>
            <BackLink />
            {drawer && (
              <div className={styles.sheet} data-size={sheet} data-no-swipe>
                <div className={styles.sheetBar}>
                  <button
                    type="button" className={styles.grip} aria-expanded={sheetOpen}
                    onClick={() => setSheet(sheetOpen ? 'peek' : 'half')}
                  >
                    <i aria-hidden="true" />
                    {sheetOpen ? '收起問卷' : cooking ? `打開進度・${potText}` : `打開問卷・步驟 ${step + 1}／${STEPS.length} ${STEPS[step].short}`}
                  </button>
                  {sheetOpen && (
                    <button type="button" className={styles.sheetSize} onClick={() => setSheet(sheet === 'full' ? 'half' : 'full')}>
                      {sheet === 'full' ? '縮小' : '放大'}
                    </button>
                  )}
                </div>
                {/* 收起時內容不顯示也不能用鍵盤走進去，但表單狀態還在 */}
                <div
                  className={styles.sheetBody} hidden={!sheetOpen}
                  // 手機鍵盤會蓋住下半個畫面：開始打字時把抽屜拉到最高，正在填的欄位才不會被擋住
                  onFocus={(event) => { if (layout.phone && sheet === 'half' && event.target.matches(TYPING)) setSheet('full') }}
                >
                  {body}
                </div>
              </div>
            )}
            {askCancel && (
              <ConfirmDialog
                title="要取消這次生成嗎？" confirmLabel="取消生成" cancelLabel="繼續煮"
                busy={generation.cancel.isPending}
                error={generation.cancel.isError ? errorMessage(generation.cancel.error) : undefined}
                onConfirm={() => generation.cancel.mutate(undefined, { onSuccess: () => setAskCancel(false) })}
                onCancel={() => setAskCancel(false)}
              >
                問卷會保留，可以修改後再煮。
              </ConfirmDialog>
            )}
          </SceneHud>
        </div>
      )}
    >
      <Placed x={POT.x} y={POT.y} width={POT.width} k={0.7}>
        <Pot view="top" state={pot} />
      </Placed>
      <Placed x={POT_TAG.x} y={layout.phone ? POT_TAG.phoneY : POT_TAG.y} k={0.7} className={styles.potTag}>
        <span role="status">{potText}</span>
      </Placed>
      {stagesByPot && job && (
        <Placed
          x={STAGE_CARD.x} y={stageCardTop(view.w, view.h, layout.stop)} width={STAGE_CARD.width} k={0.7}
          className={styles.stageCard}
        >
          <section aria-label="鍋裡的進度">
            <h3>鍋裡的進度</h3>
            <StageList job={job} />
          </section>
        </Placed>
      )}
      {area && (
        <Placed
          x={PAPER.x} y={area.top} width={PAPER.width} k={0.4} className={styles.paper}
          style={{ height: artHeightPercent(area.height) } as CSSProperties}
        >
          <div className={styles.paperInner} data-no-swipe>{body}</div>
        </Placed>
      )}
    </Room>
  )
}
