/**
 * 第一步（左頁）：這一餐誰要一起吃、誰帶便當。對應舊前端食譜庫選菜的第一步。
 * 右頁是這本書怎麼用的說明（GuidePage）。
 */
import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { MEAL_LABELS, type MealType, type Member } from '../../api/types'
import { MEALS, type Picks } from './bookLogic'
import styles from './RecipeBookScene.module.css'

type PeoplePageProps = {
  members: readonly Member[]
  mealType: MealType
  onMealType: (meal: MealType) => void
  picks: Picks
  onPick: (memberId: string, patch: Partial<Picks[string]>) => void
  dining: number
  bento: number
  /** 生成中或已採用：只能看 */
  readOnly: boolean
  saving: boolean
  /** 鎖定原因、儲存失敗等訊息（放在表單上方） */
  notice?: ReactNode
  onNext: () => void
}

export function PeoplePage({
  members, mealType, onMealType, picks, onPick, dining, bento, readOnly, saving, notice, onNext,
}: PeoplePageProps) {
  return (
    <>
      <header className={styles.pageHead}>
        <h2>這一餐，誰要吃？</h2>
        <p>勾選這餐要一起吃的人與要帶便當的人；份量會依每個人的食量自動調整。</p>
      </header>

      <div className={styles.scroll}>
        {notice}
        {members.length === 0 && (
          <p className={styles.paperNote}>
            還沒有家庭成員。<Link to="/family">先新增成員</Link>
          </p>
        )}
        <label className={styles.mealField}>
          <span>這一餐</span>
          <select value={mealType} disabled={readOnly} onChange={(event) => onMealType(event.target.value as MealType)}>
            {MEALS.map((meal) => <option key={meal} value={meal}>{MEAL_LABELS[meal]}</option>)}
          </select>
        </label>

        <ul className={styles.people}>
          {members.map((member) => {
            const pick = picks[member.id] ?? { dining: false, bento: false }
            return (
              <li key={member.id} className={pick.dining || pick.bento ? styles.personOn : undefined}>
                <span className={styles.personName}>
                  <b>{member.display_name}</b>
                  <small>食量 {member.portion}n</small>
                </span>
                <label className={styles.tick}>
                  <input
                    type="checkbox" checked={pick.dining} disabled={readOnly}
                    aria-label={`${member.display_name}一起吃`}
                    onChange={(event) => onPick(member.id, { dining: event.target.checked })}
                  />
                  <span>一起吃</span>
                </label>
                <label className={styles.tick}>
                  <input
                    type="checkbox" checked={pick.bento} disabled={readOnly}
                    aria-label={`${member.display_name}帶便當`}
                    onChange={(event) => onPick(member.id, { bento: event.target.checked })}
                  />
                  <span>帶便當</span>
                </label>
              </li>
            )
          })}
        </ul>
      </div>

      <footer className={styles.pageFoot}>
        <span aria-live="polite">用餐 {dining} 人・便當 {bento} 個</span>
        <button
          type="button" className={`${styles.ink} ${styles.inkPrimary}`}
          disabled={saving || dining + bento === 0} aria-busy={saving || undefined} onClick={onNext}
        >
          {saving ? '儲存中…' : '下一步：挑菜'}
        </button>
      </footer>
    </>
  )
}

/** 第一步的右頁：這本書怎麼用。 */
export function GuidePage({ stapleName }: { stapleName: string }) {
  return (
    <>
      <header className={styles.pageHead}>
        <h2>從食譜庫挑菜</h2>
        <p>不用填預算和口味，直接翻書挑想吃的菜。</p>
      </header>
      <div className={styles.scroll}>
        <ol className={styles.guide}>
          <li><b>誰要吃</b>在左頁勾選一起吃的人和帶便當的人。</li>
          <li><b>挑菜</b>翻食譜書，把想吃的菜夾進右下角的托盤（最多 10 道）。含有成員過敏或不吃的食材的菜會標出來，不能選。</li>
          <li><b>結果</b>按確定後，會依人數調整份量、算出整餐營養；通過就可以採用。</li>
        </ol>
        <p className={styles.caption}>沒挑飯、麵類的菜時會配一份{stapleName}。</p>
      </div>
    </>
  )
}
