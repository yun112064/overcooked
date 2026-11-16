/**
 * 點轉盤上的器皿後翻出來的做法與食材（舊前端的 DishDetail 與結果頁「全家配方」那一段）。
 * 用量是整餐的量，由後端取整好；這裡只排版。
 */
import type { MealResult } from '../../../api/types'
import { StatusChip } from '../../../components/StatusChip'
import { UnknownValue } from '../../../components/UnknownValue'
import { groupIngredients, highSodium, ingredientAmount, stapleAmount, stapleProblem, stepLines } from '../resultText'
import styles from './panels.module.css'

type Dish = MealResult['dishes'][number]

export function DishDetail({ dish, index, readOnly }: { dish: Dish; index: number; readOnly: boolean }) {
  const sodium = highSodium(dish.high_sodium, dish.sodium_mg_per_serving)
  const groups = groupIngredients(dish.ingredients)
  const lines = stepLines(dish.steps, dish.steps_display)
  return (
    <div className={styles.stack}>
      {dish.cover_url && <img className={styles.cover} src={`/api${dish.cover_url}`} alt={dish.dish_name} loading="lazy" />}
      <p className={styles.tags}>
        <span className={styles.index}>第 {index + 1} 道</span>
        {dish.cooking_method && <span>{dish.cooking_method}</span>}
        {dish.specified && <StatusChip tone="info">指定菜</StatusChip>}
        {sodium && <StatusChip tone="warn">{sodium.tag}</StatusChip>}
      </p>
      {sodium && <p className={styles.hint}>{sodium.detail}。{!readOnly && '這只是提醒，仍可照常採用。'}</p>}

      <section>
        <h3>食材<small>整餐用量</small></h3>
        {groups.length === 0 && <p className={styles.hint}>這道菜沒有食材資料。</p>}
        {groups.map(({ group, items }) => (
          <div key={group || '_'}>
            {group && <h4>{group}</h4>}
            <ul className={styles.ingredients}>
              {items.map((i, k) => {
                const text = ingredientAmount(i)
                return (
                  <li key={`${i.food_id}-${k}`}>
                    <span>{i.name}</span>
                    {text === '無法換算' ? <UnknownValue label="無法換算" /> : <span>{text}</span>}
                  </li>
                )
              })}
            </ul>
          </div>
        ))}
      </section>

      {lines.length > 0 && (
        <section>
          <h3>做法</h3>
          <ol className={styles.steps}>{lines.map((line, k) => <li key={k}>{line}</li>)}</ol>
        </section>
      )}
    </div>
  )
}

/** 主食（飯桶）：計入營養、不計入價格；算不出份量時寫明原因。 */
export function StapleDetail({ staple }: { staple: NonNullable<MealResult['staple']> }) {
  const text = stapleAmount(staple)
  return (
    <div className={styles.stack}>
      {text !== null
        ? <p className={styles.lead}>{text}</p>
        : <p className={styles.problem} role="alert">{stapleProblem(staple.label, staple.unresolved_reason)}，不可採用</p>}
      <p className={styles.hint}>
        計入營養驗證，不計入價格{staple.review_status === 'pending_review' ? '；份量設定待營養審核' : ''}
      </p>
    </div>
  )
}
