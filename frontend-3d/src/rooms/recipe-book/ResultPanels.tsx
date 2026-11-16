/**
 * 「結果」那一步展開的三個面板：每人營養、食材小知識、送出生成。
 * 內容照舊前端的 PersonNutrition、MenuPreviewPanel（小知識）、GenerationPanel、T04DataPanel。
 * 小知識只照後端給的字顯示，來源固定寫在每一則旁邊；未知的營養不畫長條。
 */
import { useMutation } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, errorMessage } from '../../api/client'
import { NUTRIENTS, rice, toNumber, withinTarget } from '../../api/nutrition'
import {
  MEAL_LABELS, type Job, type MealRequest, type NutritionPreview, type PersonNutrition, type PersonPortion,
} from '../../api/types'
import { Button } from '../../components/Button'
import { ErrorNotice, Notice } from '../../components/Notice'
import { Panel } from '../../components/Panel'
import { Spinner } from '../../components/Spinner'
import { StatusChip, type ChipTone } from '../../components/StatusChip'
import { UnknownValue } from '../../components/UnknownValue'
import { Pot } from '../../pot/Pot'
import { potLabel, potState } from '../../pot/potState'
import {
  BAR_NOTES, STAGE_LABELS, TIP_UNAVAILABLE, amountText, barTone, clock, feedbackWeightText, reference, safeHref, shareText,
  sourceName, tipSites,
} from './previewText'
import type { Generation, ServiceState } from './useGeneration'
import type { MenuPreview } from './useMenuPreview'
import styles from './RecipeBookScene.module.css'

// ---- 每人營養 ----

const KIND_LABELS = { dining: '用餐', bento: '便當' } as const

/** 這一份占每日熱量的比例（與建議比例並列）。熱量或每日目標未知時不顯示。 */
function EnergyLine({ portion, daily, label }: { portion: PersonPortion; daily: PersonNutrition['daily']; label: string }) {
  const mealRatio = toNumber(portion.meal_ratio) ?? 0
  const energy = toNumber(portion.intake.energy_kcal)
  const energyDaily = daily.energy_kcal?.value ? toNumber(daily.energy_kcal.value) : null
  if (energy === null || !energyDaily) return null
  const share = energy / energyDaily
  const within = mealRatio > 0 && withinTarget('energy_kcal', share / mealRatio)
  return (
    <p className={within ? styles.good : undefined}>
      {label}約占每日熱量 <b>{Math.round(share * 100)}%</b>
      {' '}<small className={styles.muted}>建議約 {Math.round(mealRatio * 100)}%</small>
    </p>
  )
}

/** 這一份分到的量：每道菜的份數（整份＝1，全家加總為 1）與主食克數／碗數。 */
function PortionLine({ portion, dishName, staple }: { portion: PersonPortion; dishName: Map<string, string>; staple: string | null }) {
  const share = toNumber(portion.dish_share)
  const portionText = share === null ? '份數未知' : `${share.toFixed(2)} 份`
  return (
    <p>
      {portion.dish_ids.map((id) => `${dishName.get(id) ?? id} ${portionText}`).join('、')}
      {portion.staple_g && `、${staple ?? '主食'} ${rice(portion.staple_g)}`}
    </p>
  )
}

/** 七項營養素：攝取量、每日建議與占比長條。缺值寫「未知」，不畫長條。 */
function NutrientRows({ portion, daily }: { portion: PersonPortion; daily: PersonNutrition['daily'] }) {
  const mealRatio = toNumber(portion.meal_ratio) ?? 0
  return (
    <dl className={styles.personRows}>
      {NUTRIENTS.map(({ key, label, unit }) => {
        const value = toNumber(portion.intake[key])
        const item = daily[key]
        const ref = reference(item, unit)
        const partial = toNumber(portion.known_partial[key])
        const ratio = ref && value !== null ? value / ref.base : null
        const tone = ratio !== null && item ? barTone(item.kind, ratio, mealRatio) : null
        return (
          <div key={key}>
            <dt>{label}</dt>
            <dd>
              <span className={styles.personValue}>
                {value !== null
                  ? <b>{amountText(value, unit)} {unit}</b>
                  : <UnknownValue detail={partial !== null ? `已知 ${amountText(partial, unit)} ${unit}` : undefined} />}
                {ref && (
                  <small title={[item?.label, item?.caveat].filter(Boolean).join('；')}>
                    每日 {ref.text}{value !== null && `・${shareText(value, ref)}`}
                  </small>
                )}
              </span>
              {ref && ratio !== null && tone ? (
                <span
                  className={styles.bar} role="img"
                  aria-label={`占每日建議 ${Math.round(ratio * 100)}%，這一餐建議約占 ${Math.round(mealRatio * 100)}%`}
                >
                  <i className={styles[tone]} style={{ width: `${Math.min(ratio, 1) * 100}%` }} />
                  <u style={{ left: `${Math.min(mealRatio, 1) * 100}%` }} />
                </span>
              ) : ref && value === null ? (
                <span className={`${styles.bar} ${styles.barUnknown}`} aria-hidden="true" />
              ) : null}
              {/* 糖、鈉超標不能只靠長條的顏色：和餐桌、冰箱一樣寫成字 */}
              {tone && BAR_NOTES[tone] && (
                <small className={tone === 'overDay' ? styles.barNoteOver : styles.barNote}>{BAR_NOTES[tone]}</small>
              )}
              {item?.caveat && <small className={styles.muted}>{item.caveat}</small>}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

/** 便當另列，預設收合；只帶便當不在家吃時直接展開。 */
function BentoSection({ person, alwaysOpen, dishName, staple }: {
  person: PersonNutrition; alwaysOpen: boolean; dishName: Map<string, string>; staple: string | null
}) {
  const [open, setOpen] = useState(alwaysOpen)
  const bento = person.bento
  if (!bento) return null
  const meal = MEAL_LABELS[bento.meal] ?? '午餐'
  return (
    <div className={alwaysOpen ? undefined : styles.bento}>
      <p className={styles.personHead}>
        <StatusChip>便當・{meal}</StatusChip>
        <small className={styles.muted}>食量 {toNumber(bento.n) ?? '未知'}n・不計入這一餐</small>
        {!alwaysOpen && (
          <button type="button" className={styles.linkButton} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? '收起' : '查看便當營養'}
          </button>
        )}
      </p>
      <EnergyLine portion={bento} daily={person.daily} label={`便當（${meal}）`} />
      {open && (
        <>
          <PortionLine portion={bento} dishName={dishName} staple={staple} />
          <NutrientRows portion={bento} daily={person.daily} />
        </>
      )}
    </div>
  )
}

function PersonCard({ person, name, dishName, staple }: {
  person: PersonNutrition; name: string; dishName: Map<string, string>; staple: string | null
}) {
  const dining = person.dining
  return (
    <article className={styles.person}>
      <h3 className={styles.personHead}>
        {name}
        {person.kinds.map((kind) => <StatusChip key={kind}>{KIND_LABELS[kind]}</StatusChip>)}
        {dining && <small className={styles.muted}>食量 {toNumber(dining.n) ?? '未知'}n</small>}
      </h3>
      {dining ? (
        <>
          <EnergyLine portion={dining} daily={person.daily} label="這一餐" />
          <PortionLine portion={dining} dishName={dishName} staple={staple} />
          <NutrientRows portion={dining} daily={person.daily} />
          <BentoSection person={person} alwaysOpen={false} dishName={dishName} staple={staple} />
        </>
      ) : (
        <>
          <p className={styles.muted}>這一餐不在家吃，只帶便當。</p>
          <BentoSection person={person} alwaysOpen dishName={dishName} staple={staple} />
        </>
      )}
    </article>
  )
}

type PeoplePanelProps = {
  nutrition: NutritionPreview | undefined
  /** 成員編號 → 名字 */
  names: Map<string, string>
  /** 食譜編號 → 菜名 */
  dishName: Map<string, string>
  staple: string | null
  onClose: () => void
}

export function PeoplePanel({ nutrition, names, dishName, staple, onClose }: PeoplePanelProps) {
  const people = nutrition?.people ?? []
  const source = nutrition?.daily_reference_meta?.source
  const sourceUrl = safeHref(source?.url)
  return (
    <Panel title="每人營養" side="left" onClose={onClose}>
      <div className={styles.stack}>
      <p className={styles.panelLead}>這一餐吃到的量／每日建議攝取量</p>
      {people.length === 0 ? (
        <Notice>{nutrition ? '這份菜單沒有每個人的營養資料。' : '整餐營養算出來之後，這裡會列出每個人吃到的量。'}</Notice>
      ) : (
        <>
          {people.map((person) => (
            <PersonCard
              key={person.member_id} person={person} dishName={dishName} staple={staple}
              name={names.get(person.member_id) ?? person.name ?? '成員'}
            />
          ))}
          <p className={styles.footnote}>
            每人應吃的量依各自的當餐目標分配整餐（盛菜時大人、小孩份量不同；每道菜整份＝1，所有人與便當合計為 1）；
            帶便當的份另外列出，不計入這一餐；長條上的刻度是這一餐建議占每日的比例（早餐 30%、午餐 30%、晚餐 40%，再乘上食量 n）。
            熱量與蛋白質的每日建議：成人依國健署公式，未滿 19 歲依國健署分齡表；
            其餘依{source && sourceUrl
              ? <a href={sourceUrl} target="_blank" rel="noopener noreferrer">{source.publisher}建議</a>
              : source ? `${source.publisher}建議` : '國健署建議'}由每日熱量推算；
            糖為 TFDA 總糖，對照的是添加糖上限，僅供參考。
          </p>
        </>
      )}
      </div>
    </Panel>
  )
}

// ---- 食材小知識 ----

export function TipsPanel({ preview, onClose }: { preview: MenuPreview; onClose: () => void }) {
  const { tips, passed, nutrition } = preview
  const data = tips.data
  const sites = tipSites(data)
  return (
    <Panel title="食材小知識" side="left" onClose={onClose}>
      <div className={styles.stack}>
      {sites && <p className={styles.panelLead}>資料來源：{sites}</p>}
      {!nutrition.data ? (
        <Notice>整餐營養算出來之後才會有小知識。</Notice>
      ) : !passed ? (
        <Notice>營養小知識只針對通過營養計算的菜單提供；可調整指定菜色、道數或主食後再試。</Notice>
      ) : tips.isError ? (
        <ErrorNotice error={tips.error} onRetry={() => { void tips.refetch() }} retrying={tips.isFetching} />
      ) : !data ? (
        <Spinner label="正在產生營養小知識…" />
      ) : data.status === 'unavailable' ? (
        <Notice tone="warn" title="小知識暫時拿不到">
          {TIP_UNAVAILABLE[data.reason ?? ''] ?? '服務不可用'}菜單、份量與能不能採用都不受影響。
        </Notice>
      ) : data.items.length === 0 ? (
        <Notice>這份菜單的食材目前沒有審核過的營養知識。</Notice>
      ) : (
        <ul className={styles.tips}>
          {data.items.map((tip) => (
            <li key={tip.ingredient_id}>
              <h3>{tip.ingredient_name ?? tip.ingredient_id}</h3>
              <p>{tip.tip_zh}</p>
              {/* 來源固定寫在每一則下面，不收起來 */}
              <p className={styles.tipSource}>
                {tip.citations.length === 0 && '來源：後端沒有提供'}
                {tip.citations.map((citation, index) => {
                  const href = safeHref(citation.source_url)
                  return (
                    <span key={citation.knowledge_id}>
                      {index > 0 && '、'}來源：{sourceName(citation.source_label, citation.source_url)}
                      {href && <> <a href={href} target="_blank" rel="noopener noreferrer">原文</a></>}
                    </span>
                  )
                })}
              </p>
            </li>
          ))}
        </ul>
      )}
      </div>
    </Panel>
  )
}

// ---- 送出生成 ----

/** 已等待時間：以伺服器回傳的秒數為準（每次輪詢以 key 重建），兩次輪詢之間每秒自行累加。 */
function Waited({ job }: { job: Job }) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const limit = job.timeout_seconds
  return (
    <p className={styles.muted}>
      已等待 {clock((job.waited_seconds ?? 0) + tick)}
      {limit ? `（${job.status === 'queued' ? '排隊' : '這次嘗試'}超過 ${Math.round(limit / 60)} 分鐘會自動中止，問卷會保留）` : ''}
    </p>
  )
}

const SERVICE_LABEL: Record<ServiceState['status'], string> = { online: '在線', offline: '離線', not_configured: '未設定' }
const SERVICE_TONE: Record<ServiceState['status'], ChipTone> = { online: 'ok', offline: 'warn', not_configured: 'neutral' }

// 來源回饋（T04）：後端回傳的形狀，只有這個面板用到
type Source = {
  source_id: string; provider: string; name: string; source_url: string | null
  per_100g: Record<string, string | null>; state: string | null; cooking_method: string | null
  included_in_original_aggregate: boolean | null; binding_kind: string
  confirmation_required?: boolean; confirmation_question?: string | null
}
type Feedback = {
  input_version: number; message: string
  ingredients: {
    recipe_id: string; line_id: string; name: string; status: string
    whole_recipe_pre_cooking_grams: string | null; weight_semantics?: string | null; sources: Source[]
  }[]
  recipes: { recipe_id: string; name: string; servings_raw: string; servings_basis: string }[]
}
const SOURCE_STATUS: Record<string, string> = {
  source_available_pending_review: '已有必要營養來源，待核對',
  source_candidate_needs_confirmation: '找到參考樣品，食材規格需確認',
  source_values_incomplete: '來源資料仍不完整',
  source_missing: '尚缺來源',
}
const SOURCE_NUTRIENTS = [
  ['energy_kcal', '熱量', 'kcal'], ['protein_g', '蛋白質', 'g'], ['fat_g', '脂肪', 'g'], ['carbohydrate_g', '碳水', 'g'],
  ['sugars_g', '糖', 'g'], ['sodium_mg', '鈉', 'mg'], ['dietary_fiber_g', '纖維', 'g'],
] as const

function SourceList({ sources }: { sources: Source[] }) {
  const [expanded, setExpanded] = useState(false)
  // 歷史配方推導的數字不當成來源展示
  const usable = sources.filter((s) => s.provider !== 'recipe_dataset')
  return (
    <ul className={styles.sources}>
      {(expanded ? usable : usable.slice(0, 2)).map((source) => {
        const href = safeHref(source.source_url)
        return (
          <li key={source.source_id}>
            <p>{source.provider} · {source.name}（{source.state ?? '狀態待確認'}{source.cooking_method ? `／${source.cooking_method}` : ''}）</p>
            {source.confirmation_required && (
              <Notice tone="warn">{source.confirmation_question ?? '此來源的食材規格尚未確認，只供比較。'}</Notice>
            )}
            <p className={styles.muted}>
              每 100 g：{SOURCE_NUTRIENTS.map(([key, label, unit]) =>
                `${label} ${source.per_100g[key] == null ? '未知' : `${source.per_100g[key]} ${unit}`}`).join(' · ')}
            </p>
            {source.included_in_original_aggregate === false && <p className={styles.warn}>原來源標記未納入彙整，尚待確認。</p>}
            {href && <p><a href={href} target="_blank" rel="noopener noreferrer">原始來源</a></p>}
          </li>
        )
      })}
      {usable.length > 2 && (
        <li>
          <button type="button" className={styles.linkButton} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
            {expanded ? '收合來源' : `檢視全部 ${usable.length} 份來源`}
          </button>
        </li>
      )}
      {usable.length === 0 && <li className={styles.muted}>尚無可展示的官方／產品每 100 g 來源；歷史配方推導不作替代。</li>}
    </ul>
  )
}

/** 檢查候選菜單每樣食材的營養資料來源（只讀取、不改任何資料）。 */
function SourceCheck({ row }: { row: MealRequest }) {
  const check = useMutation({
    mutationFn: async () => {
      const retrieval = (await api.get<{ selected: { recipe_id: string }[]; status: string }>(`/meal-requests/${row.id}/recipes`)).data
      if (!retrieval.selected?.length) throw new Error('目前條件查無可檢查的資料庫菜單，請調整問卷後重試。')
      return (await api.post<Feedback>(`/meal-requests/${row.id}/t04-data`, {
        request_version: row.version, recipe_ids: retrieval.selected.map((r) => r.recipe_id),
      })).data
    },
  })
  // 換了版本之後，上一版查到的來源不再顯示
  const result = check.data?.input_version === row.version ? check.data : null
  return (
    <section className={styles.sourceCheck}>
      <Button disabled={!row.complete} busy={check.isPending} onClick={() => check.mutate()}>
        {check.isPending ? '正在檢查來源…' : '檢查候選菜單的營養資料'}
      </Button>
      {check.isError && (
        <Notice tone="warn">
          {check.error instanceof Error && !('response' in check.error) ? check.error.message : errorMessage(check.error)}
        </Notice>
      )}
      {result && (
        <>
          <Notice>{result.message} 下列數字為來源每 100 g 值，整餐營養與烹調轉換仍待驗證。</Notice>
          {result.recipes.map((recipe) => (
            <div key={recipe.recipe_id}>
              <h3>{recipe.name} · 原配方 {recipe.servings_raw}{recipe.servings_basis === 'batch' ? '（批次不作人份）' : ''}</h3>
              {result.ingredients.filter((i) => i.recipe_id === recipe.recipe_id).map((ingredient) => (
                <div key={ingredient.line_id} className={styles.sourceItem}>
                  <p>
                    {ingredient.name} · {feedbackWeightText(ingredient)} ·{' '}
                    {SOURCE_STATUS[ingredient.status] ?? ingredient.status}
                  </p>
                  <SourceList sources={ingredient.sources} />
                </div>
              ))}
            </div>
          ))}
        </>
      )}
    </section>
  )
}

type GenerationPanelProps = {
  row: MealRequest
  generation: Generation
  onClose: () => void
}

/**
 * 送出生成並看進度。有結果時連到餐桌看整桌菜（價格、採買、採用都在那裡）。
 * 查無資料庫菜單時後端會自動改由 AI 生成，不另外詢問。
 */
export function GenerationPanel({ row, generation, onClose }: GenerationPanelProps) {
  const { job, running, forThisVersion, confirmed, hasResult, team, latestResultVersion } = generation
  const failedJob = !!job && forThisVersion && job.status === 'failed'
  const pot = potState({ request: row, job })
  return (
    <Panel
      title="送出生成" side="left" onClose={onClose}
      footer={(
        <>
          <Button variant="primary" disabled={!generation.canSubmit} busy={generation.submitting} onClick={() => generation.submit(false)}>
            {failedJob ? '重新送出生成' : '送出生成'}
          </Button>
          {running && <Button busy={generation.cancelling} onClick={generation.cancel}>取消生成</Button>}
          {hasResult && !confirmed && (
            <Button disabled={!generation.canRegenerate || generation.submitting} onClick={() => generation.submit(true)}>重新生成</Button>
          )}
        </>
      )}
    >
      <div className={styles.stack}>
      <p className={styles.panelLead}>
        把這張單送去生成，會算出價格與採買清單；煮好之後到餐桌看整桌菜。只想照資料庫菜單吃的話，直接在托盤上採用就好。
      </p>

      <div className={styles.potRow}>
        <div className={styles.pot}><Pot view="side" state={pot} /></div>
        <div>
          {job && forThisVersion ? (
            <>
              <p role="status">
                生成狀態：<b>{STAGE_LABELS[job.stage ?? ''] ?? job.status}</b>
                {' '}<small className={styles.muted}>第 {job.attempts}／{job.max_attempts} 次嘗試</small>
              </p>
              {running && <span className={styles.progress} aria-hidden="true" />}
              {running && <Waited key={`${job.id}:${job.status}:${job.waited_seconds}`} job={job} />}
            </>
          ) : (
            <p role="status">{potLabel(pot)}</p>
          )}
        </div>
      </div>

      {/* 工作狀態讀不到：寫出來並給重試；這張單還鎖著的話背景每 2 秒會再問一次 */}
      {!!generation.jobError && (job ? (
        <Notice tone="warn" title="連線中斷">進度停在上一次讀到的階段；連上之後會自動更新。</Notice>
      ) : (
        <Notice
          tone="error" title="讀不到生成進度"
          action={(
            <Button busy={generation.jobLoading} onClick={generation.retryJob}>
              {generation.jobLoading ? '重試中…' : '再試一次'}
            </Button>
          )}
        >
          {errorMessage(generation.jobError)}
          {generation.requestLocked && ' 這張單還在生成中，連上之後會自動更新。'}
        </Notice>
      ))}
      {job && forThisVersion && job.status === 'failed' && job.error && (
        <Notice tone={job.error.code === 'CANCELLED' ? 'info' : 'error'}>{job.error.message}</Notice>
      )}
      {job && forThisVersion && job.status === 'succeeded' && (
        <Notice tone="success">
          已產生結果{job.result_summary ? `（${job.result_summary.mode_label}，第 ${job.result_summary.result_version} 版）` : ''}。
          {row.status === 'awaiting_decision' && ' 有缺少的價格或營養資料，請到餐桌決定如何處理。'}
        </Notice>
      )}
      {!!generation.submitError && <ErrorNotice error={generation.submitError} />}
      {!!generation.cancelError && <ErrorNotice error={generation.cancelError} />}

      {hasResult && !running && (
        generation.results.isError ? (
          <ErrorNotice error={generation.results.error} onRetry={() => { void generation.results.refetch() }} retrying={generation.results.isFetching} />
        ) : latestResultVersion !== null ? (
          <p>
            <Link className={styles.tableLink} to={`/plan/${row.id}/result/${latestResultVersion}`}>
              到餐桌看這桌菜（第 {latestResultVersion} 版）
            </Link>
          </p>
        ) : generation.results.isPending ? (
          <Spinner label="正在找結果…" />
        ) : (
          <Notice>還找不到這張單的結果。</Notice>
        )
      )}
      {!generation.complete && <p className={styles.muted}>補齊菜單頁列出的項目後才能送出。</p>}

      {team && (
        <p className={styles.team}>
          <small className={styles.muted}>服務狀態</small>
          <StatusChip tone={SERVICE_TONE[team.recognition.status]}>蔬菜辨識・{SERVICE_LABEL[team.recognition.status]}</StatusChip>
          <StatusChip tone={SERVICE_TONE[team.knowledge.status]}>營養知識・{SERVICE_LABEL[team.knowledge.status]}</StatusChip>
          {team.knowledge.status !== 'online' && <small className={styles.muted}>（菜單照常生成，知識說明之後可單獨重試）</small>}
        </p>
      )}

      <SourceCheck row={row} />
      </div>
    </Panel>
  )
}
