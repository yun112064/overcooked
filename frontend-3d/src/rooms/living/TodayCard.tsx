/**
 * 靠在電視櫃前的小卡：今天、目前餐別採用了什麼（DESIGN.md 5.1）。
 * 和茶几上的盤子一樣只是擺飾，不能點；內容是採用當下保存的快照，只能看。
 *
 * 三顆標示：熱量、蛋白質（各自三態），以及整餐的計算狀態（通過／超出或不足／計算不完整）。
 * 算不出來的一律寫「計算不完整」，不會顯示成 0%。
 */
import { MEAL_LABELS, type MealType } from '../../api/types'
import plateFood from '../../assets/rooms/living/plate-food.svg'
import { Placed } from '../../camera/Room'
import { NutritionBadge } from '../../components/NutritionBadge'
import { Spinner } from '../../components/Spinner'
import { StatusChip } from '../../components/StatusChip'
import { OVERALL_VERDICT_TEXT, VERDICT_TONE, overallVerdict } from '../../components/nutritionVerdict'
import { dateLabel, dishLine, dishNames, planRatios, type TodayPlan } from './today'
import styles from './TodayCard.module.css'

// 畫稿座標：電視櫃正面 624–976 × 602–680；茶几上的白瓷盤中心 (800, 790)，盤面約 192×40
const CARD = { x: 800, y: 604, width: 400 }
const FOOD = { x: 800, y: 752, width: 186 }

export type TodayMeal =
  | { status: 'loading' }
  | { status: 'error' }
  /** plan 為 null＝今天這個餐別還沒有採用的餐食；others＝同一餐別另外還有幾筆 */
  | { status: 'ready'; plan: TodayPlan | null; others: number }

type TodayCardProps = {
  meal: MealType
  /** 今天的日期（YYYY-MM-DD） */
  today: string
  state: TodayMeal
}

export function TodayCard({ meal, today, state }: TodayCardProps) {
  const mealLabel = MEAL_LABELS[meal]
  const plan = state.status === 'ready' ? state.plan : null
  const ratios = plan ? planRatios(plan) : null
  const verdict = ratios ? overallVerdict(ratios.energy, ratios.protein) : null

  return (
    <section className={styles.body} aria-label={`今天的${mealLabel}`} aria-live="polite">
      <div className={styles.head}>
        <b>今天的{mealLabel}</b>
        <small>{dateLabel(today)}</small>
        {plan && <StatusChip tone="ok">已採用</StatusChip>}
        {state.status === 'ready' && !plan && <StatusChip>還沒決定</StatusChip>}
      </div>

      {state.status === 'loading' && <Spinner label="正在看今天吃什麼…" />}
      {state.status === 'error' && <p className={styles.hint}>讀不到今天的餐單。</p>}
      {state.status === 'ready' && !plan && (
        <p className={styles.hint}>到廚房規劃一餐，採用後菜色會擺在這張桌上。</p>
      )}
      {plan && ratios && verdict && (
        <>
          <p className={styles.dishes}>{dishLine(dishNames(plan)) || '這筆紀錄沒有菜名'}</p>
          <div className={styles.badges}>
            <NutritionBadge nutrient="energy_kcal" ratio={ratios.energy} />
            <NutritionBadge nutrient="protein_g" ratio={ratios.protein} />
            <StatusChip tone={VERDICT_TONE[verdict]}>整餐 {OVERALL_VERDICT_TEXT[verdict]}</StatusChip>
          </div>
          {state.status === 'ready' && state.others > 0 && (
            <p className={styles.more}>今天的{mealLabel}另有 {state.others} 筆採用紀錄，這裡顯示最後採用的。</p>
          )}
        </>
      )}
    </section>
  )
}

/** 放進世界裡：電視櫃前的小卡，加上茶几盤子裡的菜（有採用的餐食才擺；沒有就是空盤）。 */
export function TodayTable(props: TodayCardProps) {
  const served = props.state.status === 'ready' && props.state.plan !== null
  return (
    <>
      {served && (
        <Placed {...FOOD} k={0.7}>
          <img className={styles.food} src={plateFood} alt="" draggable={false} />
        </Placed>
      )}
      <Placed {...CARD} k={0.7} className={styles.card}>
        <TodayCard {...props} />
      </Placed>
    </>
  )
}
