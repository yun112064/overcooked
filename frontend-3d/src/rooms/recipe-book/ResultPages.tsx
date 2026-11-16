/**
 * 第三步「結果」：左頁是這一餐的菜單與全家配方，右頁是整餐營養，托盤上是採用／換一份。
 * 內容與規則照舊前端的 MenuPreviewPanel：缺值寫「未知」不補 0；營養通過才可以採用；已採用就只能看。
 */
import { useMutation } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { api, errorMessage } from '../../api/client'
import { GRAMS_PER_BOWL, NUTRIENTS, ingredientAmount, rice, toNumber } from '../../api/nutrition'
import { MEAL_LABELS, type MealRequest, type NutritionPreview, type RecipeRetrieval, type RecipeSummary } from '../../api/types'
import { ErrorNotice, Notice } from '../../components/Notice'
import { NutritionBadge } from '../../components/NutritionBadge'
import { Spinner } from '../../components/Spinner'
import { StatusChip, type ChipTone } from '../../components/StatusChip'
import { UnknownValue } from '../../components/UnknownValue'
import { ratioOf } from '../../components/nutritionVerdict'
import { nutrientCell, retrievalMessage, targetNote } from './previewText'
import type { MenuPreview } from './useMenuPreview'
import styles from './RecipeBookScene.module.css'

type Dish = NonNullable<NutritionPreview['dishes']>[number]

// 食材分組的順序（分組由後端判定；沒標的算主食材）
const GROUPS = ['主食材', '佐料', '調味'] as const

function IngredientTable({ ingredients }: { ingredients: Dish['ingredients'] }) {
  const grouped = GROUPS.map((group) => ({ group, items: ingredients.filter((i) => (i.group ?? '主食材') === group) }))
    .filter((g) => g.items.length > 0)
  return (
    <div className={styles.ingredients}>
      {grouped.map(({ group, items }) => (
        <section key={group}>
          <h4>{group}</h4>
          <dl>
            {items.map((item, index) => (
              <div key={`${item.name}-${index}`}>
                <dt>{item.name}</dt>
                <dd>{ingredientAmount(item)}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  )
}

/** 全家配方：每道菜的食材（整餐用量）與步驟，最後是主食。 */
function DishList({ selected, dishes, staple, stapleName }: {
  selected: readonly RecipeSummary[]; dishes: Dish[] | undefined; staple: NutritionPreview['staple']; stapleName: string | null
}) {
  return (
    <ol className={styles.dishes}>
      {selected.map((recipe, index) => {
        const dish = dishes?.find((d) => d.recipe_id === recipe.recipe_id)
        return (
          <li key={recipe.recipe_id}>
            <span className={styles.dishNo} aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <div>
              <h4 className={styles.dishName}>
                {recipe.recipe_name}
                {recipe.category && <small>{recipe.category}</small>}
                {recipe.specified && <StatusChip tone="info">指定菜</StatusChip>}
              </h4>
              {dish && !dish.adjusted && <p className={styles.caption}>原食譜用量（營養計算未通過，未依目標調整）</p>}
              {dish && dish.ingredients.length > 0 && <IngredientTable ingredients={dish.ingredients} />}
              {dish && dish.steps.length > 0 && (
                <ol className={styles.cookSteps}>
                  {dish.steps.map((step, k) => <li key={k}>{step}</li>)}
                </ol>
              )}
            </div>
          </li>
        )
      })}
      {staple && (
        <li>
          <span className={`${styles.dishNo} ${styles.dishNoStaple}`} aria-hidden="true">主</span>
          <div>
            <h4 className={styles.dishName}>{staple.name ?? stapleName ?? '主食'}</h4>
            {staple.final_grams && <p>{rice(staple.final_grams)}</p>}
            <p className={styles.caption}>一碗 {GRAMS_PER_BOWL} 克；計入營養驗證</p>
          </div>
        </li>
      )}
    </ol>
  )
}

/** 避免重複的菜：昨天已採用的菜會排除；指定的菜照樣保留。 */
function PreviousDay({ context, selected }: {
  context: NonNullable<RecipeRetrieval['previous_day']>; selected: readonly RecipeSummary[]
}) {
  const kept = selected.filter((r) => context.specified_exceptions.includes(r.standard_dish_id)).map((r) => r.recipe_name)
  return (
    <section>
      <h3>避免重複的菜</h3>
      {context.records.length === 0 ? (
        <p>昨天（{context.previous_date}）沒有已採用紀錄，未排除任何菜。</p>
      ) : (
        <p>
          已比對昨天（{context.previous_date}）的已採用紀錄；
          {context.excluded_dishes.length > 0
            ? `已排除：${context.excluded_dishes.map((d) => d.dish_name).join('、')}。`
            : '沒有需要排除的菜。'}
        </p>
      )}
      {kept.length > 0 && (
        <p className={styles.paperNote}>
          {kept.join('、')} 與昨天重複，因為是你指定的菜所以保留；其他限制（過敏、營養等）仍照常檢查。
        </p>
      )}
    </section>
  )
}

/** 一鍵購入：把已採用餐單缺的食材帶到模擬超市（另開分頁）。 */
export function MarketCheckoutButton({ planId }: { planId: string }) {
  const handoff = useMutation({
    mutationFn: async () => {
      const result = (await api.post<{ checkout_url: string }>('/sim-market/handoffs', { plan_id: planId })).data
      const url = new URL(result.checkout_url)
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Invalid checkout URL')
      return url.href
    },
  })
  const openMarket = async () => {
    // 先同步開一個空白分頁，瀏覽器才不會把 API 回來之後的開啟當成彈出視窗擋掉
    const tab = window.open('about:blank', '_blank')
    if (tab) tab.opener = null
    try {
      const url = await handoff.mutateAsync()
      if (tab && !tab.closed) tab.location.replace(url)
      else window.location.assign(url)
    } catch {
      tab?.close()
    }
  }
  return (
    <>
      <button
        type="button" className={`${styles.ink} ${styles.inkPrimary}`} disabled={handoff.isPending}
        aria-busy={handoff.isPending || undefined} onClick={() => { void openMarket() }}
      >
        {handoff.isPending ? '開啟超市中…' : '一鍵購入'}
      </button>
      {handoff.isError && (
        <p className={styles.conflict} role="alert">{errorMessage(handoff.error, '暫時無法開啟模擬超市，請重試。')}</p>
      )}
    </>
  )
}

type MenuPageProps = {
  row: MealRequest
  dining: number
  bento: number
  preview: MenuPreview
  /** 生成中或已採用：不能重新挑菜 */
  readOnly: boolean
  onRepick: () => void
  notice?: ReactNode
}

export function MenuPage({ row, dining, bento, preview, readOnly, onRepick, notice }: MenuPageProps) {
  const { menu, nutrition, selected, adopted } = preview
  const rejected = menu.data?.rejected_count ?? 0
  return (
    <>
      <header className={styles.pageHead}>
        <h2>這一餐的菜單</h2>
        <p>
          {row.draft.meal_type ? `${MEAL_LABELS[row.draft.meal_type]}・` : ''}用餐 {dining} 人・便當 {bento} 個・
          {row.draft.specified_dishes.join('、')}
        </p>
        <div className={styles.headTools}>
          <StatusChip>資料庫菜單</StatusChip>
          {rejected > 0 && <StatusChip>已換 {rejected} 次</StatusChip>}
          {!adopted && preview.available && (
            <button type="button" className={styles.textButton} disabled={menu.isFetching} onClick={() => { void menu.refetch() }}>
              重新查詢
            </button>
          )}
          {!readOnly && <button type="button" className={styles.textButton} onClick={onRepick}>重新挑菜</button>}
        </div>
      </header>

      <div className={styles.scroll}>
        {notice}
        {!row.complete && !readOnly && row.issues.length > 0 && (
          <Notice tone="warn">{row.issues.map((issue) => issue.message).join(' ')}</Notice>
        )}
        {!preview.available ? (
          <p className={styles.caption}>問卷填寫完整後會自動顯示資料庫菜單。</p>
        ) : (
          <>
            {menu.isFetching && <Spinner label="正在查資料庫菜單…" />}
            {menu.isError && <ErrorNotice error={menu.error} onRetry={() => { void menu.refetch() }} retrying={menu.isFetching} />}
            {menu.data && !adopted && selected.length === 0 && (
              <Notice>{retrievalMessage(menu.data, row.draft.staple)}</Notice>
            )}
            {adopted && (
              <div className={styles.adopted}>
                <p><b>已採用</b>並保存為已採用餐食紀錄（不代表實際已食用）。</p>
                <div className={styles.adoptedTools}>
                  <Link className={styles.ink} to="/today">回客廳</Link>
                  <Link className={styles.ink} to={`/records?tab=adopted&date=${adopted.meal_date}`}>查看已採用紀錄</Link>
                  {adopted.status === 'adopted' && adopted.market_shortages_available && <MarketCheckoutButton planId={adopted.id} />}
                </div>
              </div>
            )}
            {selected.length > 0 && (
              <section>
                <h3>全家配方<small>整餐用量</small></h3>
                <DishList
                  selected={selected} dishes={nutrition.data?.dishes} staple={nutrition.data?.staple ?? null}
                  stapleName={row.draft.staple}
                />
              </section>
            )}
            {menu.data?.previous_day && <PreviousDay context={menu.data.previous_day} selected={selected} />}
          </>
        )}
      </div>
    </>
  )
}

const STATUS_CHIP: Record<NutritionPreview['status'], { label: string; tone: ChipTone }> = {
  nutrition_passed: { label: '通過', tone: 'ok' },
  outside_tolerance: { label: '超出或不足', tone: 'warn' },
  calculation_incomplete: { label: '計算不完整', tone: 'unknown' },
}

function Stat({ label, value, unit, note, tone = 'default' }: {
  label: string; value: string | number | null; unit?: string; note?: string | null; tone?: 'default' | 'good' | 'warn'
}) {
  return (
    <div className={styles.stat}>
      <dt>{label}</dt>
      <dd>
        {value === null ? <UnknownValue /> : <><b>{value}</b>{unit && <span> {unit}</span>}</>}
        {note && <small className={tone === 'good' ? styles.good : tone === 'warn' ? styles.warn : undefined}>{note}</small>}
      </dd>
    </div>
  )
}

/** 整餐七種營養素；缺值顯示「未知（已知 X，缺：A、B）」，不以 0 代替。 */
function NutrientTable({ all }: { all: NonNullable<NutritionPreview['nutrition_all']> }) {
  return (
    <dl className={styles.nutrients}>
      {NUTRIENTS.map(({ key, label, unit }) => {
        const cell = nutrientCell(all, key, unit)
        return (
          <div key={key}>
            <dt>{label}</dt>
            <dd>{cell.value ?? <UnknownValue detail={cell.detail ?? undefined} />}</dd>
          </div>
        )
      })}
    </dl>
  )
}

type NutritionPageProps = {
  row: MealRequest
  preview: MenuPreview
  /** 右頁最下面的三顆按鈕：每人營養、小知識、生成 */
  tools: ReactNode
}

export function NutritionPage({ row, preview, tools }: NutritionPageProps) {
  const { nutrition, selected, adopted, passed } = preview
  const n = nutrition.data
  const energy = toNumber(n?.nutrition.totals?.energy_kcal)
  const protein = toNumber(n?.nutrition.totals?.protein_g)
  const energyTarget = toNumber(n?.targets.energy_kcal)
  const proteinTarget = toNumber(n?.targets.protein_g)
  const energyInfo = targetNote('energy_kcal', energy, energyTarget, 'kcal')
  const proteinInfo = targetNote('protein_g', protein, proteinTarget, 'g')
  const chip = n ? STATUS_CHIP[n.status] : null

  return (
    <>
      <header className={styles.pageHead}>
        <h2>
          整餐營養
          {chip && <StatusChip tone={chip.tone}>{chip.label}</StatusChip>}
        </h2>
        <p>熱量不超過目標的 110%、蛋白質不低於目標的 90% 才算通過。</p>
      </header>

      <div className={styles.scroll}>
        {nutrition.isFetching && <Spinner label="計算營養中…" />}
        {nutrition.isError && (
          <ErrorNotice error={nutrition.error} onRetry={() => { void nutrition.refetch() }} retrying={nutrition.isFetching} />
        )}
        {!n && !nutrition.isFetching && !nutrition.isError && (
          <p className={styles.caption}>
            {preview.available ? '菜單出來之後，這裡會算出整餐的營養。' : '挑好菜、按確定之後，這裡會算出整餐的營養。'}
          </p>
        )}
        {n && (
          <>
            <dl className={styles.stats}>
              <Stat label="整餐熱量" value={energy === null ? null : energy.toFixed(0)} unit="kcal" note={energyInfo.note} tone={energyInfo.tone} />
              <Stat label="整餐蛋白質" value={protein === null ? null : protein.toFixed(1)} unit="g" note={proteinInfo.note} tone={proteinInfo.tone} />
              <Stat label="菜數" value={selected.length} unit="道" note={row.draft.staple ? `主食：${row.draft.staple}` : null} />
              <Stat label="用餐份數" value={n.per_person?.length ?? row.items.length} unit="份" note="含便當；份量依各人營養目標分配" />
            </dl>
            <p className={styles.badges}>
              <NutritionBadge nutrient="energy_kcal" ratio={ratioOf(energy, energyTarget)} />
              <NutritionBadge nutrient="protein_g" ratio={ratioOf(protein, proteinTarget)} />
            </p>
            {n.nutrition_all && <NutrientTable all={n.nutrition_all} />}
            {!adopted && (
              <div className={`${styles.verdict} ${passed ? styles.verdictPass : n.status === 'outside_tolerance' ? styles.verdictWarn : styles.verdictUnknown}`}>
                <p>{passed && !n.adoption_blocked ? '符合採用條件。' : n.status_message}</p>
                {n.input_issues.length > 0 && (
                  <ul>
                    {n.input_issues.map((issue, k) => <li key={`${issue.code}-${issue.recipe_id}-${k}`}>{issue.message}</li>)}
                  </ul>
                )}
              </div>
            )}
          </>
        )}
        {!!preview.adoptError && <ErrorNotice error={preview.adoptError} />}
        {!!preview.rejectError && <ErrorNotice error={preview.rejectError} />}
      </div>

      <footer className={`${styles.pageFoot} ${styles.tools}`}>{tools}</footer>
    </>
  )
}

type TrayResultProps = {
  preview: MenuPreview
  /** 生成中：不能採用 */
  locked: boolean
}

/** 托盤：採用這份菜單／不採用，換一份；已採用就蓋章。 */
export function TrayResult({ preview, locked }: TrayResultProps) {
  const { adopted } = preview
  if (adopted) {
    return (
      <>
        <p className={styles.trayHint}><b className={styles.stamp}>已採用</b>{adopted.meal_date}・{MEAL_LABELS[adopted.meal_type]}</p>
        <Link className={styles.trayAlt} to={`/records?tab=adopted&date=${adopted.meal_date}`}>看紀錄</Link>
        <Link className={styles.trayGo} to="/today">回客廳</Link>
      </>
    )
  }
  if (!preview.showActions) {
    return (
      <p className={styles.trayHint}>
        {locked ? '正在生成中，完成前不能採用。' : preview.available ? '營養算出來之後，就可以在這裡採用。' : '還沒有可以採用的菜單。'}
      </p>
    )
  }
  return (
    <>
      <button
        type="button" className={styles.trayAlt} disabled={!preview.canReject}
        aria-busy={preview.rejecting || undefined} onClick={preview.reject}
      >
        {preview.rejecting ? '換菜中…' : '不採用，換一份'}
      </button>
      <button
        type="button" className={styles.trayGo} disabled={!preview.canAdopt}
        aria-busy={preview.adopting || undefined} onClick={preview.adopt}
      >
        {preview.adopting ? '採用中…' : '採用這份菜單'}
      </button>
    </>
  )
}
