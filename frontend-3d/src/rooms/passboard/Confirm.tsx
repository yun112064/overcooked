/**
 * 第 5 步「確認」與生成中的畫面：
 * - <Summary>：問卷摘要、還缺什麼（可以跳到那一步）、全家目標預覽、必要限制。
 * - <GenerationBlock>：服務狀態、生成狀態、取消／重新生成、營養來源檢查。
 *   「開始煮」本身固定在問卷最下面那一排（場景畫的），不用捲到最底才按得到。
 * - <CookingView>：送出後（/plan/:id/cooking）鍋旁的真實階段列、已等待時間、取消。
 * 規則照舊前端的 MealRequestEditor（Summary）、GenerationPanel、T04DataPanel。
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, errorMessage } from '../../api/client'
import { MEAL_LABELS, type Job, type MealRequest, type MealResultSummary, type Member } from '../../api/types'
import { Notice } from '../../components/Notice'
import { Spinner } from '../../components/Spinner'
import { Fold, Row, Tag } from './fields'
import { feedbackWeightText } from './previewText'
import { fmt, stepOfServerField, STEPS } from './draft'
import { clock, latestResultVersion, stageLabel, stageList, submitState } from './generation'
import { SERVICE_LABEL, useTeamStatus, type ServiceState } from './teamStatus'
import type { Generation } from './useGeneration'
import styles from './Paper.module.css'

/** 必要限制：成員的過敏與不吃的食材（成員資料決定，這裡只能看）。 */
export function Restrictions({ row }: { row: MealRequest }) {
  const { ingredients, expanded_ingredients: expanded, available_but_restricted: clash } = row.restrictions
  return (
    <div className={styles.stack}>
      {ingredients.length === 0 ? (
        <p className={styles.fieldHint}>無過敏或不吃的食材</p>
      ) : (
        <ul className={styles.chips}>
          {ingredients.map((r) => {
            const allergen = r.kinds.includes('allergen')
            return (
              <li key={r.ingredient_id}>
                <Tag tone={allergen ? 'danger' : 'warn'}>
                  {r.name ?? r.ingredient_id}・{allergen ? '過敏' : '不吃'}（{r.member_names.join('、')}）
                </Tag>
              </li>
            )
          })}
        </ul>
      )}
      {expanded.length > 0 && (
        <p className={styles.fieldHint}>
          同義或同類食材也會一併排除：
          {expanded.map((r) => {
            const base = ingredients.find((i) => r.via.includes(i.ingredient_id))
            return `${r.name ?? r.ingredient_id}（${r.relation === 'broader' ? '同類' : '同義'}：${base?.name ?? ''}）`
          }).join('、')}
        </p>
      )}
      {clash.length > 0 && <Notice>家中已有的食材中，有成員過敏或不吃的項目；產生菜單時仍會排除這些食材。</Notice>}
    </div>
  )
}

/** 全家目標（預覽）：依上次儲存的成員與餐別計算。 */
export function TargetsPreview({ row }: { row: MealRequest }) {
  const preview = row.targets_preview
  if (preview.status === 'available') {
    return (
      <p className={styles.fieldHint}>
        全家目標（預覽）約 {fmt(preview.target.energy_kcal, 0)} 大卡、蛋白質 {fmt(preview.target.protein_g)} 公克，合計 {preview.total_n}n
      </p>
    )
  }
  if (preview.status === 'unavailable') return <Notice tone="warn">{preview.message}</Notice>
  return null
}

type SummaryProps = {
  row: MealRequest
  members: Member[]
  /** 跳到某一步去補資料；唯讀時不傳 */
  onGoTo?: (step: number) => void
}

export function Summary({ row, members, onGoTo }: SummaryProps) {
  const names = new Map(members.map((m) => [m.id, m.display_name]))
  const d = row.draft
  return (
    <div className={styles.stack}>
      {/* 已採用、生成中、已經有結果時不說「可以開始煮」：那時按不了，下面的生成狀態會說明現在的情況 */}
      {row.status === 'confirmed' ? null : row.complete ? (
        (row.status === 'draft' || row.status === 'failed') && <Notice tone="success">問卷已填寫完整，可以開始煮。</Notice>
      ) : (
        <Notice tone="warn" title="還有項目需要補充">
          <ul className={styles.issueList}>
            {row.issues.map((issue) => {
              const step = stepOfServerField(issue.field)
              return (
                <li key={issue.field + issue.reason}>
                  {issue.message}
                  {onGoTo && step < STEPS.length - 1 && (
                    <button type="button" className={styles.linkButton} onClick={() => onGoTo(step)}>
                      去第 {step + 1} 步
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        </Notice>
      )}
      <dl className={styles.summary}>
        <Row label="用餐">
          {d.meal_date ?? '未選日期'}・{d.meal_type ? MEAL_LABELS[d.meal_type] : '未選餐別'}・{d.dish_count ?? '?'} 道菜・主食 {d.staple ?? '未填'}
        </Row>
        <Row label="預算">{d.budget ? `${d.budget} 元` : '未填'}</Row>
        {d.specified_dishes.length > 0 && <Row label="指定菜色">{d.specified_dishes.join('、')}</Row>}
        {d.taste_preferences && <Row label="口味">{d.taste_preferences}</Row>}
        <Row label="製作項目">
          {row.items.length === 0 ? (
            <span className={styles.fieldHint}>尚未選擇成員</span>
          ) : (
            <ul className={styles.chips}>
              {row.items.map((i) => (
                <li key={i.participation_id}>
                  <Tag>{names.get(i.member_id) ?? '已刪除成員'}・{i.kind === 'bento' ? '便當' : '用餐'}（{MEAL_LABELS[i.meal]}）</Tag>
                </li>
              ))}
            </ul>
          )}
          <TargetsPreview row={row} />
        </Row>
        <Row label="必要限制"><Restrictions row={row} /></Row>
        <Row label="家中已有">
          {row.available_ingredients.length === 0
            ? <span className={styles.fieldHint}>未選擇（全部按需求量補買）</span>
            : row.available_ingredients.map((i) => i.name ?? i.ingredient_id).join('、')}
        </Row>
      </dl>
    </div>
  )
}

function TeamStatus() {
  const status = useTeamStatus()
  if (!status.data) return null
  const { recognition, knowledge } = status.data
  const tone = (s: ServiceState) => (s.status === 'online' ? 'ok' : s.status === 'offline' ? 'warn' : 'plain')
  return (
    <p className={styles.teamStatus}>
      <small>服務狀態</small>
      <Tag tone={tone(recognition)}>蔬菜辨識・{SERVICE_LABEL[recognition.status] ?? recognition.status}</Tag>
      <Tag tone={tone(knowledge)}>營養知識・{SERVICE_LABEL[knowledge.status] ?? knowledge.status}</Tag>
      {knowledge.status !== 'online' && <small>（菜單照常生成，知識說明之後可單獨重試）</small>}
    </p>
  )
}

/** 已等待時間：以伺服器回傳的秒數為準（每次輪詢以 key 重建），兩次輪詢之間每秒自行累加。 */
function Waited({ job }: { job: Job }) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const limit = job.timeout_seconds
  return (
    <p className={styles.fieldHint}>
      已等待 {clock((job.waited_seconds ?? 0) + tick)}
      {limit ? `（${job.status === 'queued' ? '排隊' : '這次嘗試'}超過 ${Math.round(limit / 60)} 分鐘會自動中止，問卷會保留）` : ''}
    </p>
  )
}

const STATE_TEXT = { done: '已完成', current: '進行中', pending: '還沒開始' } as const
const STATE_MARK = { done: '✓', current: '●', pending: '○' } as const

/** 鍋旁的階段列：只照後端回報的階段點亮，沒有百分比。 */
export function StageList({ job }: { job: Pick<Job, 'status' | 'stage'> }) {
  const { items, note } = stageList(job)
  return (
    <div className={styles.stack}>
      <ol className={styles.stages} aria-label="生成階段">
        {items.map((item) => (
          <li key={item.key} data-state={item.state} aria-current={item.state === 'current' ? 'step' : undefined}>
            <i aria-hidden="true">{STATE_MARK[item.state]}</i>
            {item.label}
            <span className="visually-hidden">（{STATE_TEXT[item.state]}）</span>
          </li>
        ))}
      </ol>
      {note && <p className={styles.fieldHint}>目前：{note}</p>}
    </div>
  )
}

// ---- 營養來源檢查（T04）----

type Source = { source_id: string; provider: string; name: string; source_url: string | null;
  per_100g: Record<string, string | null>; state: string | null; cooking_method: string | null;
  included_in_original_aggregate: boolean | null; binding_kind: string;
  confirmation_required?: boolean; confirmation_question?: string | null }
type Feedback = { input_version: number; message: string; ingredients: { recipe_id: string; line_id: string;
  name: string; status: string; whole_recipe_pre_cooking_grams: string | null; weight_semantics?: string | null;
  sources: Source[] }[];
  recipes: { recipe_id: string; name: string; servings_raw: string; servings_basis: string }[] }

const SOURCE_STATUS: Record<string, string> = {
  source_available_pending_review: '已有必要營養來源，待核對',
  source_candidate_needs_confirmation: '找到參考樣品，食材規格需確認',
  source_values_incomplete: '來源資料仍不完整',
  source_missing: '尚缺來源',
}
const SOURCE_NUTRIENTS = [['energy_kcal', '熱量', 'kcal'], ['protein_g', '蛋白質', 'g'], ['fat_g', '脂肪', 'g'],
  ['carbohydrate_g', '碳水', 'g'], ['sugars_g', '糖', 'g'], ['sodium_mg', '鈉', 'mg'], ['dietary_fiber_g', '纖維', 'g']] as const

function SourceList({ sources }: { sources: Source[] }) {
  const [expanded, setExpanded] = useState(false)
  // 歷史配方推導出來的不算來源
  const usable = sources.filter((s) => s.provider !== 'recipe_dataset')
  return (
    <div className={styles.sources}>
      {(expanded ? usable : usable.slice(0, 2)).map((s) => (
        <div key={s.source_id}>
          <p>{s.provider} · {s.name}（{s.state ?? '狀態待確認'}{s.cooking_method ? `／${s.cooking_method}` : ''}）</p>
          {s.confirmation_required && <Notice tone="warn">{s.confirmation_question ?? '此來源的食材規格尚未確認，只供比較。'}</Notice>}
          <p className={styles.fieldHint}>
            每 100 g：{SOURCE_NUTRIENTS.map(([k, label, unit]) => `${label} ${s.per_100g[k] == null ? '未知' : `${s.per_100g[k]} ${unit}`}`).join(' · ')}
          </p>
          {s.included_in_original_aggregate === false && <p className={styles.warnText}>原來源標記未納入彙整，尚待確認。</p>}
          {s.source_url && <p><a href={s.source_url} target="_blank" rel="noreferrer">原始來源</a></p>}
        </div>
      ))}
      {usable.length > 2 && (
        <button type="button" className={styles.linkButton} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
          {expanded ? '收合來源' : `檢視全部 ${usable.length} 份來源`}
        </button>
      )}
      {!usable.length && <p className={styles.fieldHint}>尚無可展示的官方／產品每 100 g 來源；歷史配方推導不作替代。</p>}
    </div>
  )
}

/** 檢查候選菜單每樣食材的營養來源（都是待核對的來源值，不代表營養計算通過）。 */
function T04DataPanel({ row }: { row: MealRequest }) {
  const check = useMutation({
    mutationFn: async () => {
      const retrieval = (await api.get<{ selected: { recipe_id: string }[]; status: string }>(`/meal-requests/${row.id}/recipes`)).data
      if (!retrieval.selected?.length) throw new Error('目前條件查無可檢查的資料庫菜單，請調整問卷後重試。')
      return (await api.post<Feedback>(`/meal-requests/${row.id}/t04-data`, {
        request_version: row.version, recipe_ids: retrieval.selected.map((r) => r.recipe_id),
      })).data
    },
  })
  // 問卷改版後，舊版的檢查結果不再顯示
  const result = check.data?.input_version === row.version ? check.data : null
  return (
    <div className={styles.stack}>
      <div>
        <button type="button" className={styles.smallButton} disabled={!row.complete || check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? '正在檢查來源…' : '檢查候選菜單的營養資料'}
        </button>
      </div>
      {check.isError && (
        <Notice tone="warn">
          {check.error instanceof Error && !('response' in check.error) ? check.error.message : errorMessage(check.error)}
        </Notice>
      )}
      {result && (
        <>
          <Notice>{result.message} 下列數字為來源每 100 g 值，整餐營養與烹調轉換仍待驗證。</Notice>
          {result.recipes.map((recipe) => (
            <Fold
              key={recipe.recipe_id} title={recipe.name}
              aside={`原配方 ${recipe.servings_raw}${recipe.servings_basis === 'batch' ? '（批次不作人份）' : ''}`}
            >
              {result.ingredients.filter((i) => i.recipe_id === recipe.recipe_id).map((i) => (
                <div key={i.line_id} className={styles.sourceItem}>
                  <p>{i.name} · {feedbackWeightText(i)} · {SOURCE_STATUS[i.status] ?? i.status}</p>
                  <SourceList sources={i.sources} />
                </div>
              ))}
            </Fold>
          ))}
        </>
      )}
    </div>
  )
}

// ---- 送出與狀態 ----

type GenerationProps = {
  row: MealRequest
  generation: Generation
  /** 按「重新生成」（「開始煮」在問卷最下面那一排） */
  onRegenerate: () => void
  /** 按「取消生成」（由場景跳出確認） */
  onAskCancel: () => void
}

export function GenerationBlock({ row, generation, onRegenerate, onAskCancel }: GenerationProps) {
  const { job, submit, cancel } = generation
  const state = submitState(row, job)
  const { own, active } = state

  // 有結果時要帶到餐桌的那個版本：問後端最新的是哪一版（補查價格、重試小知識會產生比生成工作更新的版本）；
  // 還沒問到之前先用這一版生成工作回報的
  const ownVersion = own?.status === 'succeeded' ? own.result_summary?.result_version ?? own.result_version : null
  const results = useQuery({
    queryKey: ['meal-results', row.id],
    queryFn: async () =>
      (await api.get<{ results: MealResultSummary[] }>(`/meal-requests/${row.id}/results`)).data.results,
    enabled: state.hasResult && !active,
  })
  const resultVersion = latestResultVersion(results.data, ownVersion)

  return (
    <section className={styles.stack} aria-label="生成狀態">
      <TeamStatus />
      {own && (
        <div className={styles.stack}>
          <p>
            生成狀態：<b>{stageLabel(own)}</b>
            <small className={styles.aside}>第 {own.attempts}／{own.max_attempts} 次嘗試</small>
          </p>
          {active && <Waited key={`${own.id}:${own.status}:${own.waited_seconds}`} job={own} />}
          {active && <p><Link className={styles.smallButton} to={`/plan/${row.id}/cooking`}>看鍋裡的進度</Link></p>}
          {own.status === 'failed' && own.error && (
            <Notice tone={own.error.code === 'CANCELLED' ? 'info' : 'error'} title={own.error.code === 'CANCELLED' ? undefined : '這一鍋沒煮成'}>
              {own.error.message}
            </Notice>
          )}
          {own.status === 'succeeded' && (
            <Notice tone="success">
              已產生結果{own.result_summary ? `（${own.result_summary.mode_label}，第 ${own.result_summary.result_version} 版）` : ''}。
              {row.status === 'awaiting_decision' && ' 有缺少的價格或營養資料，請到餐桌決定如何處理。'}
            </Notice>
          )}
        </div>
      )}
      {!own && row.status === 'failed' && <Notice tone="error" title="這一鍋沒煮成">上一次生成失敗了，問卷還在，可以調整後再煮一次。</Notice>}
      {!own && row.status === 'awaiting_decision' && <Notice tone="warn">有缺少的價格或營養資料，請到餐桌決定如何處理。</Notice>}

      {state.hasResult && !active && (
        resultVersion == null && results.isError ? <Notice tone="error">{errorMessage(results.error)}</Notice>
          : resultVersion != null ? (
            <p>
              <Link className={`${styles.smallButton} ${styles.smallPrimary}`} to={`/plan/${row.id}/result/${resultVersion}`}>
                {state.confirmed ? '看已採用的這一桌' : '看這一桌'}
              </Link>
            </p>
          ) : results.isFetching ? <Spinner label="正在找這一桌…" /> : null
      )}

      <T04DataPanel row={row} />
      {cancel.isError && <Notice tone="error">{errorMessage(cancel.error)}</Notice>}

      {!state.confirmed && (active || state.hasResult) && (
        <div className={styles.cookActions}>
          {active && (
            <button type="button" className={styles.smallButton} disabled={cancel.isPending} onClick={onAskCancel}>
              取消生成
            </button>
          )}
          {state.hasResult && (
            <button type="button" className={styles.smallButton} disabled={!state.canRegenerate || submit.isPending} onClick={onRegenerate}>
              {submit.isPending ? '送出中…' : '重新生成'}
            </button>
          )}
        </div>
      )}
      {!row.complete && !state.confirmed && <p className={styles.fieldHint}>補齊上方項目後才能開始煮。</p>}
    </section>
  )
}

type CookingProps = {
  row: MealRequest
  generation: Generation
  /** 階段列已經貼在鍋旁（桌機）時傳 false，這裡就不再列一次 */
  showStages: boolean
  onAskCancel: () => void
}

/** 送出後的畫面：鍋蓋蓋著，這裡列出後端回報的階段。 */
export function CookingView({ row, generation, showStages, onAskCancel }: CookingProps) {
  const { job, latest, cancel, active } = generation
  const d = row.draft
  return (
    <div className={styles.stack}>
      <p>
        {d.meal_date ?? '未選日期'}・{d.meal_type ? MEAL_LABELS[d.meal_type] : '未選餐別'}
        {d.dish_count ? `・${d.dish_count} 道菜` : ''}
      </p>
      {!job ? (
        // 一次都沒讀到就失敗：背景每幾秒會再問一次，也可以自己按（按了會馬上重問，不用等）
        latest.isError ? (
          <Notice
            tone="error" title="讀不到鍋裡的進度"
            action={<button type="button" className={styles.smallButton} onClick={() => { void latest.refetch() }}>再試一次</button>}
          >
            {errorMessage(latest.error)} 連上之後會自動更新。
          </Notice>
        ) : <Spinner label="正在讀取進度…" />
      ) : (
        <>
          {/* 階段變化由鍋的小牌（role="status"）唸出來，這裡不重複 */}
          <p>現在：<b>{stageLabel(job)}</b><small className={styles.aside}>第 {job.attempts}／{job.max_attempts} 次嘗試</small></p>
          {showStages ? <StageList job={job} /> : <p className={styles.fieldHint}>每個階段的進度貼在鍋的右邊。</p>}
          {active && <Waited key={`${job.id}:${job.status}:${job.waited_seconds}`} job={job} />}
          {/* 問不到最新進度：階段列停在上一次問到的地方，不自己往前推 */}
          {latest.isError && <Notice tone="warn" title="連線中斷">進度停在上一次讀到的階段；連上之後會自動更新。</Notice>}
        </>
      )}
      <p className={styles.fieldHint}>煮的時候不能改問卷；等不及可以取消，問卷會保留。</p>
      {cancel.isError && <Notice tone="error">{errorMessage(cancel.error)}</Notice>}
      <div className={styles.cookActions}>
        <button type="button" className={styles.smallButton} disabled={!active || cancel.isPending} onClick={onAskCancel}>
          {cancel.isPending ? '取消中…' : '取消生成'}
        </button>
        <Link className={styles.smallButton} to={`/plan/${row.id}`}>看問卷內容</Link>
      </div>
    </div>
  )
}
