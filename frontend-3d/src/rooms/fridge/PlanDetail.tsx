/**
 * 一份已採用餐單的完整明細（放在面板裡）。顯示的都是採用當下保存的快照，唯讀，來源之後更新也不會變。
 * 兩種紀錄各有自己的內容（搬自舊前端的兩個歷史頁）：
 * - 生成結果：價格、三大營養素比例、每道菜的食材與步驟、每人營養、昨日比對、食材小知識（一定附來源）。
 * - 資料庫菜單：整餐熱量與蛋白質、全家配方（主食材／佐料／調味）、每人份量與營養。
 */
import { useState } from 'react'
import { GRAMS_PER_BOWL, NUTRIENTS, ingredientAmount, rice, toNumber } from '../../api/nutrition'
import { NUTRIENTS as RESULT_NUTRIENTS, amount, nutrientText } from '../../api/resultNutrition'
import type { DailyReference, MealResult } from '../../api/types'
import { MEAL_LABELS } from '../../api/types'
import { Money } from '../../components/Money'
import { Notice } from '../../components/Notice'
import { NutritionBadge } from '../../components/NutritionBadge'
import { StatusChip } from '../../components/StatusChip'
import { UnknownValue } from '../../components/UnknownValue'
import { ratioOf } from '../../components/nutritionVerdict'
import {
  barTone, energyShare, fmt, groupIngredients, macroSplit, peopleFromResult, peopleFromSnapshot, percentText,
  reference, resultAmount, safeHref, sodiumNote, stapleAmount, stapleProblem, stepLines,
  type PersonView, type PortionView,
} from './nutritionView'
import { isGenerationPlan, type AdoptedPlan, type PlanDish } from './records'
import styles from './PlanDetail.module.css'

type Nutrition = MealResult['nutrition']
type Source = { publisher: string; url: string } | undefined

export function PlanDetail({ plan }: { plan: AdoptedPlan }) {
  const generation = isGenerationPlan(plan)
  return (
    <div className={styles.detail}>
      <p className={styles.meta}>
        採用於 {new Date(plan.adopted_at).toLocaleString('zh-TW')}。這是採用當下保存的快照，只能看、不能改；不代表實際已食用。
      </p>
      <p className={styles.chips}>
        {generation ? (
          <>
            <StatusChip tone={plan.ai_generated ? 'info' : 'neutral'}>{plan.mode_label ?? plan.mode}</StatusChip>
            {plan.price_exception_accepted && <StatusChip tone="warn">部分價格未知</StatusChip>}
            <StatusChip>結果第 {plan.result_version} 版</StatusChip>
          </>
        ) : <StatusChip>資料庫菜單</StatusChip>}
      </p>
      {generation ? <GenerationBody plan={plan} /> : <SnapshotBody plan={plan} />}
    </div>
  )
}

// ---- 生成結果的紀錄 ----

function GenerationBody({ plan }: { plan: AdoptedPlan }) {
  const prices = plan.prices?.status === 'computed' ? plan.prices : null
  const knowledge = plan.knowledge?.status === 'available' ? plan.knowledge.items : []
  return (
    <>
      {plan.fallback_message && <Notice>{plan.fallback_message}</Notice>}

      <section>
        <h3>價格</h3>
        {prices ? (
          <>
            <dl className={styles.stats}>
              <div><dt>按比例成本</dt><dd><Money value={prices.cost_total} known={prices.known_cost_total} /></dd></div>
              <div><dt>整包花費</dt><dd><Money value={prices.purchase_total} known={prices.known_purchase_total} /></dd></div>
              <div><dt>預算</dt><dd>{prices.budget !== null ? <Money value={prices.budget} /> : <UnknownValue label="未設定" />}</dd></div>
              <div><dt>菜數</dt><dd>{plan.dishes.length} 道</dd></div>
            </dl>
            <p className={styles.caption}>整包花費是把缺的食材整包買回來的錢；按比例成本只算這一餐用掉的份量。兩個數字各看各的，不相加。</p>
          </>
        ) : <p className={styles.caption}>這份紀錄沒有價格計算結果：<UnknownValue /></p>}
      </section>

      {plan.nutrition && <MacroBar nutrition={plan.nutrition} />}

      {/* 小知識放在菜色前面：菜的做法與每人營養很長，放最後幾乎沒人捲得到 */}
      {knowledge.length > 0 && (
        <section className={styles.knowledge}>
          <h3>食材小知識</h3>
          <ul>
            {knowledge.map((k) => (
              <li key={k.ingredient_id}>
                <b>{k.ingredient_name ?? k.ingredient_id}</b>{k.explanation}
                {/* 來源一定跟在說明旁邊，不能省略 */}
                <span className={styles.cites}>
                  {k.citations.length === 0 && '（沒有附來源）'}
                  {k.citations.map((c, i) => {
                    const href = safeHref(c.source_url)
                    return (
                      <span key={`${c.knowledge_id}-${i}`}>
                        {c.attribution ?? `來源：${c.knowledge_id} ${c.source_version}`}
                        {href ? <a href={href} target="_blank" rel="noreferrer">原文</a> : c.attribution && c.source_url ? ` ${c.source_url}` : null}
                      </span>
                    )
                  })}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3>菜色</h3>
        <ul className={styles.dishes}>
          {plan.dishes.map((dish, i) => <ResultDish key={dish.standard_dish_id ?? i} dish={dish} />)}
          {/* 主食不在菜色清單裡，但整餐熱量與上面的比例都有算它，要列出來才對得起來 */}
          {plan.staple && <ResultStaple staple={plan.staple} />}
        </ul>
      </section>

      {/* 與結果頁相同的 7 種營養素與未知顯示（以 null 判斷，0 照常顯示） */}
      {plan.nutrition && (
        <section>
          <h3>每人營養（這一餐／每日建議）</h3>
          <ResultPeople nutrition={plan.nutrition} />
        </section>
      )}

      {plan.previous_day && (
        <p className={styles.caption}>
          昨日比對（{plan.previous_day.previous_date ?? '沒有日期'}）：
          {plan.previous_day.excluded_dishes.length > 0
            ? `排除 ${plan.previous_day.excluded_dishes.map((d) => d.dish_name).join('、')}`
            : '沒有排除的菜'}
          {plan.previous_day.specified_repeats.length > 0 && `；指定保留 ${plan.previous_day.specified_repeats.join('、')}`}
        </p>
      )}

    </>
  )
}

function ResultDish({ dish }: { dish: PlanDish }) {
  const sodium = sodiumNote(dish.high_sodium, dish.sodium_mg_per_serving)
  const steps = stepLines(dish.steps, dish.steps_display)
  const sourceHref = safeHref(dish.recipe_source?.source_url)
  return (
    <li>
      <h4>
        {dish.dish_name}
        {dish.cooking_method && <small>{dish.cooking_method}</small>}
        {dish.aliases && dish.aliases.length > 0 && <small>又名 {dish.aliases.join('、')}</small>}
        {sodium && <StatusChip tone="warn">{sodium.label}</StatusChip>}
      </h4>
      {/* 高鈉只是提醒，不影響採用；說明寫成文字，不藏在滑鼠提示裡 */}
      {sodium && <p className={styles.caption}>{sodium.detail}</p>}
      {dish.ingredients && groupIngredients(dish.ingredients).map(([group, items]) => (
        <p key={group || '_'} className={styles.ingredients}>
          {group && <b>{group}：</b>}
          {items.map((i) => `${i.name} ${resultAmount(i)}`).join('、')}
        </p>
      ))}
      {steps.length > 0 && <ol className={styles.steps}>{steps.map((s, i) => <li key={i}>{s}</li>)}</ol>}
      {dish.recipe_source && (
        <p className={styles.caption}>
          食譜來源：{sourceHref
            ? <a href={sourceHref} target="_blank" rel="noreferrer">{dish.recipe_source.source_name}</a>
            : dish.recipe_source.source_name}
        </p>
      )}
    </li>
  )
}

/** 生成結果的主食：份量的寫法和餐桌相同；算不出來寫原因，不寫成 0。 */
function ResultStaple({ staple }: { staple: NonNullable<AdoptedPlan['staple']> }) {
  const amount = stapleAmount(staple)
  return (
    <li>
      <h4>{staple.label}<small>主食</small></h4>
      <p className={styles.ingredients}>
        {amount ?? <UnknownValue label="份量未知" detail={stapleProblem(staple.unresolved_reason)} />}
      </p>
      <p className={styles.caption}>
        計入營養驗證，不計入價格{staple.review_status === 'pending_review' && '；份量設定待營養審核'}
      </p>
    </li>
  )
}

/** 整餐三大營養素的熱量比例：一條堆疊長條＋每項的百分比、克數與參考範圍（文字並列，不只靠顏色）。 */
function MacroBar({ nutrition }: { nutrition: Nutrition }) {
  const rows = macroSplit(nutrition)
  return (
    <section>
      <h3>三大營養素熱量比例</h3>
      {rows === null ? (
        // 有任何一項缺值就不畫長條：缺一塊的比例會讓人誤會
        <p className={styles.caption}><UnknownValue detail="有營養素缺值，算不出比例" /></p>
      ) : (
        <>
          <div className={styles.macroBar} role="img" aria-label={rows.map((r) => `${r.label} ${Math.round(r.pct)}%`).join('、')}>
            {rows.map((r) => <i key={r.key} className={styles[r.key]} style={{ width: `${r.pct}%` }} />)}
          </div>
          <ul className={styles.macros}>
            {rows.map((r) => (
              <li key={r.key}>
                <b><i className={styles[r.key]} aria-hidden="true" />{r.label} {Math.round(r.pct)}%</b>
                <small className={r.verdict ? styles.off : undefined}>
                  {Math.round(r.grams)} g・建議 {r.range[0]}–{r.range[1]}%{r.verdict && `・${r.verdict}`}
                </small>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}

/** 每人這一餐吃到多少，對照每人每日建議。舊結果沒有每人資料時改顯示整餐表。 */
function ResultPeople({ nutrition }: { nutrition: Nutrition }) {
  const [showWhole, setShowWhole] = useState(false)
  const people = peopleFromResult(nutrition)
  if (people.length === 0) return <WholeMealTable nutrition={nutrition} />
  return (
    <>
      <People people={people} source={nutrition.daily_reference?.source} />
      <button type="button" className={styles.toggle} aria-expanded={showWhole} onClick={() => setShowWhole((v) => !v)}>
        {showWhole ? '收起整餐合計' : '查看整餐合計'}
      </button>
      {showWhole && <WholeMealTable nutrition={nutrition} />}
    </>
  )
}

/** 整餐營養表：目標、估算、差距皆為整餐值；只有熱量與蛋白質有目標。 */
function WholeMealTable({ nutrition }: { nutrition: Nutrition }) {
  const n = toNumber(nutrition.total_n)
  const cell = (text: string) => (text.startsWith('未知') ? <span className={styles.unknownText}>{text}</span> : text)
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <caption>整餐合計{n !== null && `（共 ${n} 份）`}</caption>
        <thead><tr><th scope="col">項目</th><th scope="col">目標</th><th scope="col">估算</th><th scope="col">差距</th></tr></thead>
        <tbody>
          {RESULT_NUTRIENTS.map(({ key, label, unit, required }) => (
            <tr key={key}>
              <th scope="row">{label}</th>
              <td>{required ? cell(amount(nutrition.target?.[key as 'energy_kcal' | 'protein_g'], unit)) : '—'}</td>
              <td>{cell(nutrientText(nutrition, key, unit))}</td>
              <td>{required ? cell(amount(nutrition.gap?.[key as 'energy_kcal' | 'protein_g'], unit)) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---- 資料庫菜單的紀錄 ----

function SnapshotBody({ plan }: { plan: AdoptedPlan }) {
  const s: NonNullable<AdoptedPlan['snapshot']> = plan.snapshot ?? {}
  const totals = s.nutrition?.totals
  const energy = toNumber(totals?.energy_kcal ?? plan.energy_kcal)
  const protein = toNumber(totals?.protein_g ?? plan.protein_g)
  const targetEnergy = toNumber(s.targets?.energy_kcal)
  const targetProtein = toNumber(s.targets?.protein_g)
  const selected = s.selected ?? []
  const people = peopleFromSnapshot(s)
  return (
    <>
      <section>
        <h3>整餐</h3>
        <dl className={styles.stats}>
          <div>
            <dt>整餐熱量</dt>
            <dd>{energy === null ? <UnknownValue /> : `${Math.round(energy)} kcal`}</dd>
            {targetEnergy !== null && <dd className={styles.sub}>目標 {Math.round(targetEnergy)} kcal</dd>}
          </div>
          <div>
            <dt>整餐蛋白質</dt>
            <dd>{protein === null ? <UnknownValue /> : `${protein.toFixed(1)} g`}</dd>
            {targetProtein !== null && <dd className={styles.sub}>目標 {Math.round(targetProtein)} g</dd>}
          </div>
          <div>
            <dt>菜數</dt>
            <dd>{plan.dishes.length} 道</dd>
            {s.staple_choice && <dd className={styles.sub}>主食：{s.staple_choice}</dd>}
          </div>
          <div><dt>用餐份數</dt><dd>{s.people ? `${s.people.length} 人` : <UnknownValue label="沒有紀錄" />}</dd></div>
        </dl>
        {/* 有目標才比；算不出來就是「計算不完整」，不當成通過 */}
        {(targetEnergy !== null || targetProtein !== null) && (
          <p className={styles.chips}>
            <NutritionBadge nutrient="energy_kcal" ratio={ratioOf(energy, targetEnergy)} />
            <NutritionBadge nutrient="protein_g" ratio={ratioOf(protein, targetProtein)} />
          </p>
        )}
      </section>

      <section>
        <h3>全家配方</h3>
        {selected.length > 0 ? (
          <ol className={styles.dishes}>
            {selected.map((recipe) => {
              const dish = s.dishes?.find((d) => d.recipe_id === recipe.recipe_id)
              return (
                <li key={recipe.recipe_id}>
                  <h4>
                    {recipe.recipe_name}
                    {recipe.category && <small>{recipe.category}</small>}
                    {recipe.specified && <StatusChip tone="info">指定菜</StatusChip>}
                  </h4>
                  {dish && !dish.adjusted && <p className={styles.caption}>原食譜用量（營養計算未通過，未依目標調整）</p>}
                  {dish && dish.ingredients.length > 0 && <RecipeIngredients ingredients={dish.ingredients} />}
                  {dish && dish.steps.length > 0 && <ol className={styles.steps}>{dish.steps.map((step, i) => <li key={i}>{step}</li>)}</ol>}
                </li>
              )
            })}
            {s.staple && (
              <li>
                <h4>{s.staple.name ?? s.staple_choice ?? '主食'}<small>主食</small></h4>
                {s.staple.final_grams && <p className={styles.ingredients}>{rice(s.staple.final_grams)}</p>}
                <p className={styles.caption}>一碗 {GRAMS_PER_BOWL} 克；計入營養驗證</p>
              </li>
            )}
          </ol>
        ) : (
          <>
            {/* 最早期的紀錄只保存了菜名 */}
            <p className={styles.caption}>這筆紀錄只保存了菜名，沒有食材用量與步驟。</p>
            <ul className={styles.plainList}>{plan.dishes.map((d, i) => <li key={i}>{d.dish_name}</li>)}</ul>
          </>
        )}
      </section>

      {people.length > 0 && (
        <section>
          <h3>每人營養（這一餐／每日建議）</h3>
          <People people={people} source={s.daily_reference_meta?.source} portions />
        </section>
      )}
    </>
  )
}

type RecipeIngredient = NonNullable<NonNullable<AdoptedPlan['snapshot']>['dishes']>[number]['ingredients'][number]
const RECIPE_GROUPS = ['主食材', '佐料', '調味'] as const

/** 每道菜的食材依「主食材／佐料／調味」分組（後端判定；沒標的算主食材）。 */
function RecipeIngredients({ ingredients }: { ingredients: RecipeIngredient[] }) {
  return (
    <div className={styles.groups}>
      {RECIPE_GROUPS.map((group) => {
        const items = ingredients.filter((i) => (i.group ?? '主食材') === group)
        if (items.length === 0) return null
        return (
          <div key={group}>
            <h5>{group}</h5>
            <ul>
              {items.map((i, k) => <li key={`${i.name}-${k}`}><span>{i.name}</span><span>{ingredientAmount(i)}</span></li>)}
            </ul>
          </div>
        )
      })}
    </div>
  )
}

// ---- 每人營養（兩種紀錄共用） ----

function People({ people, source, portions = false }: { people: PersonView[]; source: Source; portions?: boolean }) {
  const href = safeHref(source?.url)
  return (
    <>
      <ul className={styles.people}>
        {people.map((person) => <PersonCard key={person.id} person={person} />)}
      </ul>
      <p className={styles.caption}>
        每人應吃的量依各自的當餐目標分配整餐（盛菜時大人、小孩份量不同{portions && '；每道菜整份＝1，所有人與便當合計為 1'}）；
        帶便當的份另外列出，不計入這一餐；長條上的刻度是這一餐建議占每日的比例（早餐 30%、午餐 30%、晚餐 40%，再乘上食量 n）。
        熱量與蛋白質的每日建議：成人依國健署公式，未滿 19 歲依國健署分齡表；
        其餘依{source && href ? <a href={href} target="_blank" rel="noreferrer">{source.publisher}建議</a> : `${source?.publisher ?? '國健署'}建議`}由每日熱量推算；
        糖為 TFDA 總糖，對照的是添加糖上限，僅供參考。
      </p>
    </>
  )
}

const KIND_LABELS = { dining: '用餐', bento: '便當' } as const

function PersonCard({ person }: { person: PersonView }) {
  const { dining } = person
  return (
    <li className={styles.person}>
      <h4>
        {person.name}
        {person.kinds.map((k) => <StatusChip key={k}>{KIND_LABELS[k]}</StatusChip>)}
        {dining && dining.n !== null && <small>食量 {dining.n}n</small>}
      </h4>
      {dining ? (
        <>
          <EnergyLine portion={dining} daily={person.daily} label="這一餐" />
          {dining.portionText && <p className={styles.ingredients}>{dining.portionText}</p>}
          <NutrientRows portion={dining} daily={person.daily} />
          <Bento person={person} initiallyOpen={false} />
        </>
      ) : (
        <>
          <p className={styles.caption}>這一餐不在家吃，只帶便當。</p>
          <Bento person={person} initiallyOpen />
        </>
      )}
    </li>
  )
}

/** 這一份占每日熱量的比例（與建議比例並列）；算得出來就附上三態判定。 */
function EnergyLine({ portion, daily, label }: { portion: PortionView; daily: Record<string, DailyReference>; label: string }) {
  const share = energyShare(portion, daily)
  if (share === null) return null
  const mealRatio = portion.mealRatio
  return (
    <p className={styles.energy}>
      {label}約占每日熱量 <b>{Math.round(share * 100)}%</b>
      {mealRatio !== null && <small>建議約 {Math.round(mealRatio * 100)}%</small>}
      {mealRatio !== null && mealRatio > 0 && <NutritionBadge nutrient="energy_kcal" ratio={share / mealRatio} />}
    </p>
  )
}

const OVER_TEXT = { normal: null, overMeal: '超過這一餐的份額', overDay: '超過一整天的上限' } as const

/** 七項營養素：攝取量、每日建議與占比長條。未知不畫長條。 */
function NutrientRows({ portion, daily }: { portion: PortionView; daily: Record<string, DailyReference> }) {
  const mealRatio = portion.mealRatio ?? 0
  return (
    <dl className={styles.nutrients}>
      {NUTRIENTS.map(({ key, label, unit }) => {
        const value = toNumber(portion.intake[key])
        const ref = reference(daily[key], unit)
        const partial = toNumber(portion.knownPartial[key])
        const percent = ref && value !== null ? percentText(value, ref) : null
        const drawBar = ref !== null && value !== null && ref.base > 0
        const tone = drawBar ? barTone(value / ref.base, mealRatio, daily[key].kind) : 'normal'
        const note = [daily[key]?.label, daily[key]?.caveat].filter(Boolean).join('；')
        return (
          <div key={key}>
            <dt>{label}</dt>
            <dd>
              <span className={styles.value}>
                {value !== null
                  ? `${fmt(value, unit)} ${unit}`
                  : <UnknownValue detail={partial !== null ? `已知 ${fmt(partial, unit)} ${unit}` : undefined} />}
              </span>
              {ref && <span className={styles.ref} title={note || undefined}>每日 {ref.text}{percent && `・${percent}`}</span>}
              {drawBar && (
                <span className={`${styles.shareBar} ${styles[tone]}`} aria-hidden="true">
                  <i style={{ width: `${Math.min(value / ref.base, 1) * 100}%` }} />
                  <u style={{ left: `${Math.min(mealRatio, 1) * 100}%` }} />
                </span>
              )}
              {OVER_TEXT[tone] && <small className={styles.off}>{OVER_TEXT[tone]}</small>}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}

/** 便當另列，預設收合；只帶便當不在家吃時直接展開。 */
function Bento({ person, initiallyOpen }: { person: PersonView; initiallyOpen: boolean }) {
  const [open, setOpen] = useState(initiallyOpen)
  const { bento } = person
  if (!bento) return null
  const meal = MEAL_LABELS[bento.meal] ?? '午餐'
  return (
    <div className={initiallyOpen ? undefined : styles.bento}>
      <p className={styles.chips}>
        <StatusChip>便當・{meal}</StatusChip>
        <small>{bento.n !== null && `食量 ${bento.n}n・`}不計入這一餐</small>
        {!initiallyOpen && (
          <button type="button" className={styles.toggle} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? '收起' : '查看便當營養'}
          </button>
        )}
      </p>
      <EnergyLine portion={bento} daily={person.daily} label={`便當（${meal}）`} />
      {open && (
        <>
          {bento.portionText && <p className={styles.ingredients}>{bento.portionText}</p>}
          <NutrientRows portion={bento} daily={person.daily} />
        </>
      )}
    </div>
  )
}
