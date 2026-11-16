/**
 * 翻開的食譜書（/pick、/pick/:id）＝食譜庫選菜（DESIGN.md 5.3）。
 * 流程與舊前端的 RecipePickerPage 相同，三步都在同一本書上：
 *   0 誰要吃（左頁勾成員）→ 1 挑菜（左頁清單、右頁細節、托盤確定）→ 2 結果（左頁菜單、右頁營養、托盤採用）
 * 第一次儲存才建立這張單並換到 /pick/:id；之後每次儲存都帶版本號，版本不同由後端拒絕。
 *
 * 舊流程在這裡不問預算、口味與已有食材，所以沒有「拍冰箱」的捷徑。
 *
 * 位置都由 bookFraming 依畫面大小算：視窗太扁、書的上緣被裁掉時，標籤與頁面內容會往下讓。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useId, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import { api, apiError, errorStatus } from '../../api/client'
import type {
  CatalogDish, MealRequest, MealRequestDraft, MealType, Member, RecipeCatalog, StapleOptions,
} from '../../api/types'
import z0 from '../../assets/rooms/recipe-book/z0.svg'
import z1 from '../../assets/rooms/recipe-book/z1.svg'
import z2 from '../../assets/rooms/recipe-book/z2.svg'
import z3 from '../../assets/rooms/recipe-book/z3.svg'
import { Placed, Room, useRoom, type HotspotItem } from '../../camera/Room'
import { ErrorNotice, Notice } from '../../components/Notice'
import { Spinner } from '../../components/Spinner'
import { mealRequestQuery, membersQuery } from '../../session/queries'
import { SceneHud } from '../../shell/SceneHud'
import { BookmarkTabs, DetailPage, FilterPanel, ListPage, TrayPicks } from './CatalogPages'
import { GuidePage, PeoplePage } from './PeoplePage'
import { MenuPage, NutritionPage, TrayResult } from './ResultPages'
import { GenerationPanel, PeoplePanel, TipsPanel } from './ResultPanels'
import { LEFT_PAGE, RIGHT_PAGE, TOP_Y, TRAY, artHeight, bookLayout, type BookLayout, type SafeArea } from './bookFraming'
import {
  NO_FILTERS, STEPS, bookmarkValues, chosenNames, clampPage, countPicks, filterCount, filterDishes, hasStapleDish,
  initialStep, pageCount, pickerDraft, savedSelections, stapleFor, toggleValue, canToggle,
  type FacetKey, type Filters, type Picks, type Step,
} from './bookLogic'
import { generationStatus, tipsStatus } from './resultStatus'
import { useGeneration } from './useGeneration'
import { useMenuPreview } from './useMenuPreview'
import styles from './RecipeBookScene.module.css'

const LAYERS = [z0, z1, z2, z3]

// 兩個停靠點（左頁／右頁與托盤）在每一步的名字
const STOP_LABELS: Record<Step, readonly [string, string]> = { 0: ['誰要吃', '說明'], 1: ['清單', '細節'], 2: ['菜單', '營養'] }

/**
 * 瀏海與底部橫條佔掉的高度（px）。右上角的選單、底部的分頁與物件列在樣式裡用 env() 往裡讓，
 * 這裡要知道讓了多少才排得準；env() 在 JS 讀不到，借一個看不見的元素量。
 */
function safeArea(): SafeArea {
  // 沒有瀏覽器（測試直接畫成 HTML）：當成沒有瀏海
  if (typeof document === 'undefined') return { top: 0, bottom: 0 }
  const probe = document.createElement('div')
  probe.style.cssText = 'position:fixed;visibility:hidden;pointer-events:none;'
    + 'padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)'
  document.body.append(probe)
  const style = getComputedStyle(probe)
  const area = { top: parseFloat(style.paddingTop) || 0, bottom: parseFloat(style.paddingBottom) || 0 }
  probe.remove()
  return area
}

type Screen = { w: number; h: number; safe: SafeArea }

function measureScreen(): Screen {
  return { w: window.innerWidth, h: window.innerHeight, safe: safeArea() }
}

/** 依畫面大小算出停靠點與各個東西的位置；視窗大小變了（含手機轉向、網址列收起來）就重算。 */
function useBookLayout(step: Step): BookLayout {
  const [screen, setScreen] = useState(measureScreen)
  useEffect(() => {
    const measure = () => setScreen((prev) => {
      const next = measureScreen()
      const same = prev.w === next.w && prev.h === next.h && prev.safe.top === next.safe.top && prev.safe.bottom === next.safe.bottom
      return same ? prev : next
    })
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])
  return useMemo(() => bookLayout(screen.w, screen.h, STOP_LABELS[step], screen.safe), [screen, step])
}

type PanelId = 'filters' | 'people' | 'tips' | 'generate'

export default function RecipeBookScene() {
  const { id } = useParams()
  const queryClient = useQueryClient()
  const request = useQuery({ ...mealRequestQuery(id ?? ''), enabled: !!id })
  const members = useQuery(membersQuery)
  const row = id ? request.data : undefined
  const requestError = id ? request.error : null
  const failure = requestError ?? members.error

  // 登入過期或還沒選家庭：請外層重新確認登入狀態，它會把人帶回大門或玄關
  const sessionLost = !!failure && (errorStatus(failure) === 401 || apiError(failure)?.code === 'HOUSEHOLD_NOT_SELECTED')
  useEffect(() => {
    if (sessionLost) void queryClient.invalidateQueries({ queryKey: ['me'] })
  }, [sessionLost, queryClient])

  const retry = () => { void (requestError ? request.refetch() : members.refetch()) }
  const retrying = request.isFetching || members.isFetching
  // 擋住整個畫面的只有「資料根本沒讀到」。已經翻開之後背景重讀失敗（切回分頁、網路重連）不能把書換掉：
  // 托盤裡還沒按確定的菜、篩選、翻到第幾頁都只存在畫面上，換掉就全沒了。這時留在原地，另外寫一則訊息。
  const blocking = (!row ? requestError : null) ?? (!members.data ? members.error : null)
  if (blocking) {
    return (
      <Shell>
        <ErrorNotice error={blocking} onRetry={retry} retrying={retrying} />
        {!!id && !!requestError && (
          <Notice action={<Link className={styles.ink} to="/pick">開一張新的</Link>}>這張單打不開的話，可以重新挑一次。</Notice>
        )}
      </Shell>
    )
  }
  if ((id && !row) || !members.data) return <Shell><Spinner label="正在翻開食譜書…" /></Shell>
  // 完整問卷不在這本書上填：帶去單板
  if (row && row.draft.mode !== 'picker') return <Navigate to={`/plan/${row.id}`} replace />
  // key：新建後換成已存的那份時重建狀態
  return (
    <Picker
      key={row?.id ?? 'new'} initial={row ?? null} members={members.data}
      stale={failure ? { error: failure, retry, retrying } : null}
    />
  )
}

/** 背景重讀失敗：畫面上是上一次讀到的內容 */
type Stale = { error: unknown; retry: () => void; retrying: boolean }

/** 資料還沒到或讀不到時：只有書，說明寫在左頁上。 */
function Shell({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const layout = useBookLayout(1)
  const close = () => navigate('/plan')
  return (
    <Room
      name="食譜書" layers={LAYERS} stops={[layout.stops[0]]}
      hotspots={[{ id: 'close', label: '闔上書', status: '回廚房', stop: 'list', onActivate: close }]}
      hud={<SceneHud tab="pick" />}
    >
      <BookTop y={layout.tagsY} onClose={close} />
      <BookPage side="left" top={layout.pageTop} ids={[]} label="食譜書">
        <div className={styles.scroll}>{children}</div>
      </BookPage>
    </Room>
  )
}

function Picker({ initial, members, stale }: { initial: MealRequest | null; members: Member[]; stale: Stale | null }) {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [server, setServer] = useState<MealRequest | null>(initial)
  // 這張單在別的房間被動過（例如到餐桌採用了）：快取重讀回來後換成新的那一份，這裡才知道已經不能改。
  // 版本比手上舊的不收（比較晚到的舊回應）。
  const [seen, setSeen] = useState(initial)
  if (initial !== seen) {
    setSeen(initial)
    if (initial && (!server || initial.version >= server.version)) setServer(initial)
  }
  const [step, setStep] = useState<Step>(() => initialStep(initial, location.state))
  // 畫面上的選擇一開始照已存的那一份；版本衝突後重新讀取時也用同一個換算整個換掉（見 reload）
  const [seed] = useState(() => savedSelections(members, initial?.draft))
  const [mealType, setMealType] = useState<MealType>(seed.mealType)
  const [picks, setPicks] = useState<Picks>(seed.picks)
  const [stop, setStop] = useState('list')
  const [panel, setPanel] = useState<PanelId | null>(null)
  // 挑菜那一步的狀態
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  const [keyword, setKeyword] = useState('')
  const [picked, setPicked] = useState<string[]>(seed.picked)
  const [page, setPage] = useState(0)
  const [viewingId, setViewingId] = useState<string | null>(null)
  // 兩頁的 DOM id：從物件列點「清單」「細節」時把焦點移到那一頁
  const pageId = useId()
  const layout = useBookLayout(step)

  const goStep = (to: Step) => {
    setStep(to)
    setStop('list')
    setPanel(null)
  }

  const updateRequest = useCallback((row: MealRequest) => {
    setServer(row)
    queryClient.setQueryData(['meal-request', row.id], row)
    void queryClient.invalidateQueries({ queryKey: ['meal-requests'] })
  }, [queryClient])

  const staples = useQuery({
    queryKey: ['staple-options'],
    queryFn: async () => (await api.get<StapleOptions>('/meal-requests/staple-options')).data,
    staleTime: Infinity,
  })

  const save = useMutation({
    mutationFn: async (draft: MealRequestDraft) => server
      ? (await api.put<MealRequest>(`/meal-requests/${server.id}`, { version: server.version, draft })).data
      : (await api.post<MealRequest>('/meal-requests', draft)).data,
    onSuccess: updateRequest,
  })

  const serverId = server?.id
  const refresh = useCallback(async () => {
    if (!serverId) return
    try {
      updateRequest((await api.get<MealRequest>(`/meal-requests/${serverId}`)).data)
    } catch { /* 讀不到就維持現狀；下一次狀態變化會再讀 */ }
  }, [serverId, updateRequest])

  // 儲存遇到版本衝突＝別的分頁或裝置先改過這張單。重新讀取後畫面上的選擇要整個換成伺服器那一份：
  // 只換版本號的話，畫面上還留著舊的勾選，下一次儲存會帶著新的版本號把別處剛存的內容蓋掉（等於繞過版本檢查）。
  // 讀成功才收掉錯誤訊息；讀不到就留著，可以再按一次。
  const reload = useMutation({
    mutationFn: async (requestId: string) => (await api.get<MealRequest>(`/meal-requests/${requestId}`)).data,
    onSuccess: (row) => {
      const saved = savedSelections(members, row.draft)
      updateRequest(row)
      // 成員可能也被改了：哪些菜不能選要重新問
      void queryClient.invalidateQueries({ queryKey: ['recipe-catalog', row.id] })
      setMealType(saved.mealType)
      setPicks(saved.picks)
      setPicked(saved.picked)
      save.reset()
    },
  })
  // 剛換成別處存的內容、還沒再存過：提醒一下，畫面上的勾選已經不是剛才自己選的
  const reloaded = reload.isSuccess && !!server && reload.data.version === server.version

  const locked = server?.status === 'queued' || server?.status === 'running'
  const adopted = server?.status === 'confirmed'
  const readOnly = locked || adopted
  const { dining, bento } = countPicks(picks)
  const stapleName = staples.data?.options[0]?.label ?? '白飯'

  // ---- 第一步：誰要吃 ----
  const canNext = !save.isPending && dining + bento > 0
  const savePeople = async () => {
    if (!canNext) return
    // 生成中或已採用：不能改，只是翻過去看
    if (server && readOnly) { goStep(1); return }
    try {
      const row = await save.mutateAsync(pickerDraft(mealType, picks, server?.draft))
      // 第一次儲存才建立這張單：換到它的網址，之後重新整理也回得來
      if (!server) navigate(`/pick/${row.id}`, { replace: true, state: { step: 1 } })
      else goStep(1)
    } catch { /* 錯誤訊息顯示在頁面上 */ }
  }

  // ---- 第二步：挑菜 ----
  const catalog = useQuery({
    queryKey: ['recipe-catalog', serverId],
    queryFn: async () => (await api.get<RecipeCatalog>(`/meal-requests/${serverId}/recipe-catalog`)).data,
    // 翻到這一步才讀；每次翻回來都重讀一次（成員改了，哪些菜不能選也會變）
    enabled: !!serverId && step === 1,
  })
  const dishes = useMemo(() => catalog.data?.dishes ?? [], [catalog.data])
  const byName = useMemo(() => new Map(dishes.map((d) => [d.dish_name, d])), [dishes])
  const shown = useMemo(() => filterDishes(dishes, filters, keyword), [dishes, filters, keyword])
  const chosen = chosenNames(picked, byName)
  const conflicted = new Set(chosen.filter((name) => (byName.get(name)?.conflicts.length ?? 0) > 0))
  const viewing = dishes.find((d) => d.standard_dish_id === viewingId) ?? null
  const viewingChecked = !!viewing && chosen.includes(viewing.dish_name)
  const currentPage = clampPage(page, shown.length)
  const activeFilters = filterCount(filters)

  const toggleFilter = (key: FacetKey, value: string) => {
    setFilters((f) => ({ ...f, [key]: toggleValue(f[key], value) }))
    setPage(0)
  }
  const clearFilters = () => { setFilters(NO_FILTERS); setPage(0) }
  const allCategories = () => { setFilters((f) => ({ ...f, category: [] })); setPage(0) }
  const toggleDish = (dish: CatalogDish) => {
    if (readOnly || !canToggle(dish, chosen.includes(dish.dish_name), chosen.length)) return
    setPicked((p) => toggleValue(p, dish.dish_name))
  }
  const viewDish = (dish: CatalogDish) => {
    setViewingId(dish.standard_dish_id)
    // 手機一次只看得到一頁：點了菜名就把鏡頭帶到右頁
    setStop('detail')
  }

  const canConfirm = !!server && !readOnly && !save.isPending && !!staples.data && chosen.length > 0
  // 所選的菜就是整餐的菜（道數跟著改）；沒挑飯麵類時配一份主食，挑了就不另配
  const confirmDishes = async () => {
    if (!server || !staples.data || !canConfirm) return
    const staple = stapleFor(hasStapleDish(chosen, byName), staples.data)
    try {
      await save.mutateAsync({
        ...pickerDraft(mealType, picks, server.draft), specified_dishes: chosen, dish_count: chosen.length, staple,
      })
      goStep(2)
    } catch { /* 錯誤訊息顯示在頁面上 */ }
  }

  // ---- 第三步：結果 ----
  const preview = useMenuPreview(server, step === 2, () => {
    if (server) updateRequest({ ...server, status: 'confirmed' })
  })
  const generation = useGeneration(server, step === 2, refresh)
  const tipsLabel = tipsStatus(preview)
  const generationLabel = generationStatus(generation)
  const nutritionStatus = preview.nutrition.data?.status
  const nutritionLabel = nutritionStatus === 'nutrition_passed' ? '通過'
    : nutritionStatus === 'outside_tolerance' ? '超出或不足'
      : nutritionStatus === 'calculation_incomplete' ? '計算不完整' : undefined

  // ---- 共用的訊息 ----
  const conflict = save.isError && apiError(save.error)?.code === 'VERSION_CONFLICT'
  const notice = (
    <>
      {locked && <Notice>正在生成中，完成前無法修改。</Notice>}
      {adopted && (
        <Notice action={<Link className={styles.ink} to="/pick">重新挑一次</Link>}>這餐已採用，只能瀏覽；想換菜請重新挑一次。</Notice>
      )}
      {stale && (
        <ErrorNotice
          error={stale.error} fallback="最新的資料讀不到，畫面上是上一次讀到的內容。"
          onRetry={stale.retry} retrying={stale.retrying}
        />
      )}
      {save.isError && (
        <ErrorNotice
          error={save.error} retryLabel="重新讀取這張單" retrying={reload.isPending}
          onRetry={conflict && server ? () => reload.mutate(server.id) : undefined}
        />
      )}
      {reload.isError && <ErrorNotice error={reload.error} fallback="這張單讀不到，請再按一次「重新讀取這張單」。" />}
      {reloaded && (
        <Notice tone="warn" title="已換成最新的內容">
          這張單在別的地方改過，畫面上的餐別、成員和挑的菜已經換成最新存的那一份；請確認後再存一次。
        </Notice>
      )}
      {step === 1 && staples.isError && (
        <ErrorNotice
          error={staples.error} fallback="主食清單讀不到，暫時不能按確定。"
          onRetry={() => { void staples.refetch() }} retrying={staples.isFetching}
        />
      )}
    </>
  )

  const close = () => navigate('/plan')
  const focus = (side: 'left' | 'right') => () => document.getElementById(`${pageId}-${side}`)?.focus()
  // 闔上書與步驟的標籤跟著鏡頭搬到看得到的那一頁上方，所以算在目前的停靠點
  const closeItem: HotspotItem = { id: 'close', label: '闔上書', status: '回廚房', stop, onActivate: close }
  const pages = pageCount(shown.length)

  const hotspots: HotspotItem[] = step === 0 ? [
    { id: 'left', label: '誰要吃', status: `用餐 ${dining}・便當 ${bento}`, stop: 'list', onActivate: focus('left') },
    { id: 'next', label: '下一步：挑菜', stop: 'list', onActivate: () => { void savePeople() } },
    { id: 'right', label: '說明', stop: 'detail', onActivate: focus('right') },
    closeItem,
  ] : step === 1 ? [
    { id: 'left', label: '清單', status: `第 ${currentPage + 1}／${pages} 頁`, stop: 'list', onActivate: focus('left') },
    ...(currentPage > 0 ? [{ id: 'prev-page', label: '上一頁', stop: 'list', onActivate: () => setPage(currentPage - 1) }] : []),
    ...(currentPage < pages - 1 ? [{ id: 'next-page', label: '下一頁', stop: 'list', onActivate: () => setPage(currentPage + 1) }] : []),
    { id: 'filters', label: '分類', status: activeFilters > 0 ? `已選 ${activeFilters} 項` : undefined, stop: 'list', onActivate: () => setPanel('filters') },
    { id: 'right', label: '細節', status: viewing?.dish_name, stop: 'detail', onActivate: focus('right') },
    ...(viewing ? [{
      id: 'toggle', label: viewingChecked ? '拿出托盤' : '夾進托盤', stop: 'detail', onActivate: () => toggleDish(viewing),
    }] : []),
    { id: 'tray', label: '托盤', status: `確定 ${chosen.length} 道`, stop: 'detail', onActivate: () => { void confirmDishes() } },
    { id: 'steps', label: '回「誰要吃」', stop, onActivate: () => goStep(0) },
    closeItem,
  ] : [
    { id: 'left', label: '菜單', status: preview.selected.length > 0 ? `${preview.selected.length} 道` : undefined, stop: 'list', onActivate: focus('left') },
    // 生成中或已採用不能改，但還是可以翻回去看挑了什麼
    { id: 'repick', label: readOnly ? '回去看挑的菜' : '重新挑菜', stop: 'list', onActivate: () => goStep(1) },
    { id: 'right', label: '營養', status: nutritionLabel, stop: 'detail', onActivate: focus('right') },
    { id: 'people', label: '每人營養', stop: 'detail', onActivate: () => setPanel('people') },
    { id: 'tips', label: '小知識', status: tipsLabel || undefined, stop: 'detail', onActivate: () => setPanel('tips') },
    { id: 'generate', label: '生成', status: generationLabel || undefined, stop: 'detail', onActivate: () => setPanel('generate') },
    ...(preview.showActions ? [
      {
        id: 'tray', label: '採用這份菜單', status: preview.canAdopt || preview.adopting ? undefined : '還不能採用', stop: 'detail',
        onActivate: () => { if (preview.canAdopt) preview.adopt() },
      },
      { id: 'reject', label: '換一份', stop: 'detail', onActivate: () => { if (preview.canReject) preview.reject() } },
    ] : [{ id: 'tray', label: '托盤', status: preview.adopted ? '已採用' : undefined, stop: 'detail' }]),
    closeItem,
  ]

  return (
    <Room
      name="食譜書" layers={LAYERS} stops={layout.stops} stopId={stop} onStopChange={setStop} hotspots={hotspots}
      // 手機上書頁佔滿整個畫面寬，側邊的「‹ 清單／細節 ›」會壓在頁面的字上、還會把點擊接走；
      // 上方那一列也被標籤和選單佔滿，沒有空的地方可以放。換頁靠物件列的「清單」「細節」、左右滑動，點菜名也會自己翻到右頁。
      // 平板沒有物件列、被切掉的那一頁也要有東西可以按，所以留著
      edgeNav={!layout.phone}
      hud={(
        // 托盤（確定／採用）就在畫面底部吉祥物來回跑的那一條上：讓牠站在右下角（托盤右邊的檯面），不擋按鈕
        <SceneHud tab="pick" assistant="parked">
          {panel === 'filters' && step === 1 && catalog.data && (
            <FilterPanel
              facets={catalog.data.facets} filters={filters} matches={shown.length}
              onToggle={toggleFilter} onClear={clearFilters} onClose={() => setPanel(null)}
            />
          )}
          {panel === 'people' && step === 2 && (
            <PeoplePanel
              nutrition={preview.nutrition.data} staple={server?.draft.staple ?? null}
              names={new Map(members.map((m) => [m.id, m.display_name]))}
              dishName={new Map(preview.selected.map((r) => [r.recipe_id, r.recipe_name]))}
              onClose={() => setPanel(null)}
            />
          )}
          {panel === 'tips' && step === 2 && <TipsPanel preview={preview} onClose={() => setPanel(null)} />}
          {panel === 'generate' && step === 2 && server && (
            <GenerationPanel row={server} generation={generation} onClose={() => setPanel(null)} />
          )}
        </SceneHud>
      )}
    >
      <BookTop y={layout.tagsY} step={step} onStep={goStep} onClose={close} />

      <BookPage
        side="left" top={layout.pageTop} ids={['left', 'next', 'prev-page', 'next-page', 'filters', 'repick']}
        label={STOP_LABELS[step][0]} domId={`${pageId}-left`}
      >
        {step === 0 && (
          <PeoplePage
            members={members} mealType={mealType} onMealType={setMealType} picks={picks}
            onPick={(memberId, patch) => setPicks((all) => ({
              ...all, [memberId]: { ...(all[memberId] ?? { dining: false, bento: false }), ...patch },
            }))}
            dining={dining} bento={bento} readOnly={readOnly} saving={save.isPending} notice={notice}
            onNext={() => { void savePeople() }}
          />
        )}
        {step === 1 && (
          <ListPage
            loading={catalog.isPending} error={catalog.error} onRetry={() => { void catalog.refetch() }} retrying={catalog.isFetching}
            categories={catalog.data?.facets.category ?? []} inlineCategories={!layout.tabs} filters={filters}
            onToggleFilter={toggleFilter} onAllCategories={allCategories} onClearFilters={clearFilters}
            onOpenFilters={() => setPanel('filters')}
            keyword={keyword} onKeyword={(value) => { setKeyword(value); setPage(0) }}
            shown={shown} page={currentPage} onPage={setPage}
            chosen={chosen} viewingId={viewingId} onView={viewDish} onToggle={toggleDish} readOnly={readOnly}
          />
        )}
        {step === 2 && server && (
          <MenuPage
            row={server} dining={dining} bento={bento} preview={preview} readOnly={readOnly}
            onRepick={() => goStep(1)} notice={notice}
          />
        )}
      </BookPage>

      <BookPage
        side="right" top={layout.pageTop} ids={['right', 'toggle', 'people', 'tips', 'generate']}
        label={STOP_LABELS[step][1]} domId={`${pageId}-right`}
      >
        {step === 0 && <GuidePage stapleName={stapleName} />}
        {step === 1 && (
          <DetailPage
            dish={viewing} checked={viewingChecked} chosenCount={chosen.length} readOnly={readOnly}
            onToggle={toggleDish} notice={notice} stapleName={stapleName}
          />
        )}
        {step === 2 && server && (
          <NutritionPage
            row={server} preview={preview}
            tools={(
              <>
                <button type="button" className={styles.ink} onClick={() => setPanel('people')}>每人營養</button>
                <button type="button" className={styles.ink} onClick={() => setPanel('tips')}>
                  小知識{tipsLabel && <small>{tipsLabel}</small>}
                </button>
                <button type="button" className={styles.ink} onClick={() => setPanel('generate')}>
                  生成{generationLabel && <small>{generationLabel}</small>}
                </button>
              </>
            )}
          />
        )}
      </BookPage>

      {step === 1 && catalog.data && layout.tabs && (
        <BookmarkTabs
          values={bookmarkValues(catalog.data.facets.category)} selected={filters.category}
          onToggle={(value) => toggleFilter('category', value)} onAll={allCategories}
          onMore={() => setPanel('filters')} otherCount={activeFilters - filters.category.length}
        />
      )}

      <Tray clear={layout.trayClear}>
        {step === 0 && (
          <p className={styles.trayHint}>
            {server && server.draft.specified_dishes.length > 0
              ? `已挑：${server.draft.specified_dishes.join('、')}`
              : '先選好誰要吃，再把想吃的菜夾進來。'}
          </p>
        )}
        {step === 1 && (
          <TrayPicks
            chosen={chosen} conflicted={conflicted} readOnly={readOnly} saving={save.isPending}
            disabled={!canConfirm} onRemove={(name) => setPicked((p) => p.filter((n) => n !== name))}
            onConfirm={() => { void confirmDishes() }}
          />
        )}
        {step === 2 && <TrayResult preview={preview} locked={locked} />}
      </Tray>
    </Room>
  )
}

type BookTopProps = {
  /** 標籤的下緣（畫稿 y）；預設貼在書的上方 */
  y?: number
  step?: Step
  onStep?: (to: Step) => void
  onClose: () => void
}

/**
 * 闔上書、目前在第幾步。平常在書上方的檯面；書的上緣被裁掉時往下壓在書上（位置由 bookFraming 算）。
 * 放在左頁上方（205–395、400–780）；鏡頭停在右頁時整組搬到右頁上方，兩個停靠點都完整看得到。
 */
function BookTop({ y = TOP_Y, step, onStep, onClose }: BookTopProps) {
  const { stopId, flashId } = useRoom()
  const offset = stopId === 'detail' ? RIGHT_PAGE.x - LEFT_PAGE.x : 0
  return (
    <>
      <Placed x={300 + offset} y={y} width={190} k={0.4} className={styles.topSlot}>
        <button
          type="button" className={`${styles.closeTag} ${flashId === 'close' ? styles.flash : ''}`}
          aria-label="闔上書，回廚房" onClick={onClose}
        >
          <span aria-hidden="true">‹ </span>闔上書
        </button>
      </Placed>
      {step !== undefined && (
        <Placed x={590 + offset} y={y} width={380} k={0.4} className={styles.topSlot}>
          <ol className={styles.stepRail} aria-label="步驟">
            {STEPS.map((item, index) => (
              <li key={item.short} aria-current={index === step ? 'step' : undefined}>
                {index < step && onStep ? (
                  <button type="button" title={`回到「${item.title}」`} onClick={() => onStep(index as Step)}>
                    {index + 1} {item.short}
                  </button>
                ) : (
                  <span title={item.title}>{index + 1} {item.short}</span>
                )}
              </li>
            ))}
          </ol>
        </Placed>
      )}
    </>
  )
}

type BookPageProps = {
  side: 'left' | 'right'
  /** 內容的上緣（畫稿 y）：平常是紙的上緣，書的上緣被裁掉時比較低 */
  top: number
  /** 物件列裡哪些項目在這一頁上（被點到時整頁閃一下） */
  ids: readonly string[]
  label: string
  domId?: string
  children: ReactNode
}

/** 一頁書頁：內容疊在插圖的紙上（左頁到紙的下緣，右頁到托盤上緣），太長就在頁內上下捲動。 */
function BookPage({ side, top, ids, label, domId, children }: BookPageProps) {
  const { flashId } = useRoom()
  const flashing = flashId !== null && ids.includes(flashId)
  const paper = side === 'left' ? LEFT_PAGE : RIGHT_PAGE
  return (
    <Placed
      x={paper.x} y={top} width={paper.width} k={0.4} style={{ height: artHeight(paper.bottom - top) }}
      className={`${styles.page} ${side === 'left' ? styles.pageLeft : styles.pageRight} ${flashing ? styles.flash : ''}`}
    >
      <section id={domId} tabIndex={-1} aria-label={label} className={styles.pageBody}>{children}</section>
    </Placed>
  )
}

/** 壓在右頁下緣的胡桃木托盤。clear：右邊要讓出多少 px（手機上吉祥物站在托盤右端時，按鈕排在牠左邊）。 */
function Tray({ clear, children }: { clear: number; children: ReactNode }) {
  const { flashId } = useRoom()
  const flashing = flashId === 'tray' || flashId === 'reject'
  return (
    <Placed x={TRAY.x} y={TRAY.y} width={TRAY.width} k={0.7} className={`${styles.tray} ${flashing ? styles.flash : ''}`}>
      <div
        role="group" aria-label="托盤" className={styles.trayInner}
        style={clear > 0 ? { '--tray-clear': `${clear}px` } as CSSProperties : undefined}
      >
        {children}
      </div>
    </Placed>
  )
}
