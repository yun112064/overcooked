/**
 * 客廳的電視（DESIGN.md 5.1）：螢幕下緣三顆按鈕切換早／午／晚，螢幕上是那個餐別近 7 天的熱量達成長條。
 *
 * 長條只是輔助：每一天的數字另外寫成一份給讀屏的清單。
 * - 沒有採用紀錄的日子不畫長條（空著，不是 0）。
 * - 有紀錄但熱量或目標算不出來：整格斜紋虛線加「?」，不畫成有高度的長條。
 * - 超過目標 110%：換顏色並標「!」（不只靠顏色）。
 */
import { MEAL_LABELS, type MealType } from '../../api/types'
import { Placed, useRoom } from '../../camera/Room'
import { MEALS, barHeight, barScale, dayText, todayText, type DayBar } from './today'
import styles from './Tv.module.css'

// 畫稿座標：螢幕 656–944 × 396–568。這一塊往下蓋到電視櫃檯面（588），
// 多出來的邊框與腳座讓餐別按鈕的可按範圍有 44px，又不佔螢幕裡的位置。
const SCREEN = { x: 800, y: 396, width: 288 }

export type TvTrend = { status: 'loading' } | { status: 'error' } | { status: 'ready'; bars: DayBar[] }

type TvScreenProps = {
  meal: MealType
  onMeal: (meal: MealType) => void
  trend: TvTrend
}

export function TvScreen({ meal, onMeal, trend }: TvScreenProps) {
  const mealLabel = MEAL_LABELS[meal]
  const bars = trend.status === 'ready' ? trend.bars : []
  const hasRecords = bars.some((bar) => bar.state !== 'none')
  const scale = barScale(bars)

  return (
    <section className={styles.screen} aria-label={`電視：${mealLabel}近 7 天的熱量`}>
      <div className={styles.head}>
        <b>{mealLabel}・近 7 天熱量</b>
        {trend.status === 'ready' && hasRecords && <span>{todayText(bars)}</span>}
      </div>

      {trend.status === 'loading' && <p className={styles.message} role="status">讀取中…</p>}
      {trend.status === 'error' && <p className={styles.message}>讀不到紀錄</p>}
      {trend.status === 'ready' && !hasRecords && (
        <p className={styles.message}>近 7 天沒有採用的{mealLabel}，採用後這裡會出現長條</p>
      )}
      {trend.status === 'ready' && hasRecords && (
        <>
          <div className={styles.chart} aria-hidden="true">
            <div className={styles.bars}>
              <i className={styles.target} style={{ bottom: `${barHeight(1, scale)}%` }}><span>目標</span></i>
              {bars.map((bar) => (
                <div key={bar.date} className={`${styles.col} ${bar.today ? styles.now : ''}`}>
                  {bar.state === 'unknown' && <span className={`${styles.bar} ${styles.unknown}`}>?</span>}
                  {(bar.state === 'pass' || bar.state === 'over') && bar.ratio !== null && (
                    <span
                      className={`${styles.bar} ${bar.state === 'over' ? styles.over : ''}`}
                      style={{ height: `${barHeight(bar.ratio, scale)}%` }}
                    >
                      {bar.state === 'over' && '!'}
                    </span>
                  )}
                </div>
              ))}
            </div>
            <div className={styles.days}>
              {bars.map((bar) => <span key={bar.date} className={bar.today ? styles.now : undefined}>{bar.label}</span>)}
            </div>
          </div>
          <ul className="visually-hidden">
            {bars.map((bar) => <li key={bar.date}>{dayText(bar)}</li>)}
          </ul>
        </>
      )}

      <div className={styles.seg} role="group" aria-label="切換電視上的餐別">
        {MEALS.map((item) => (
          <button key={item} type="button" aria-pressed={item === meal} onClick={() => onMeal(item)}>
            <span>{MEAL_LABELS[item]}</span>
          </button>
        ))}
      </div>
    </section>
  )
}

/** 放在世界裡的電視；從物件列被點到時整台閃一下。 */
export function Tv(props: TvScreenProps) {
  const { flashId } = useRoom()
  return (
    <Placed {...SCREEN} k={0.4} className={`${styles.tv} ${flashId === 'tv' ? styles.flash : ''}`}>
      <TvScreen {...props} />
    </Placed>
  )
}
